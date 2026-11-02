"""Four-gate, fail-closed acceptance of a local CNEquity research lake."""
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

PROFILE = "cn_a_sh_sz_research_v1"
DEFAULT_PIT_ITEMS = ("roe", "net_profit")


def _gate(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", **details}


def _safe_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def run_acceptance(
    *,
    data_root: str | Path,
    start_date: str,
    end_date: str,
    pit_items: list[str] | None = None,
    profile: str = PROFILE,
) -> dict[str, Any]:
    """Run historical universe, HFQ, strict PIT and trading-state gates.

    A failure in one gate is recorded rather than aborting the report. No
    training dataset is created or unlocked by this function.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    items = list(pit_items or DEFAULT_PIT_ITEMS)
    if not items:
        raise ValueError("pit_items must contain at least one documented item code")

    from cnequity.query import load, load_with_receipt, resolve_config
    from cnequity.quality.factor_diagnostics import publish_factor_diagnostics
    from cnequity.quality.historical_validity import historical_universe_validity
    from cnequity.quality.trading_status_coverage import traded_status_evidence

    root = Path(data_root).expanduser().resolve()
    config = resolve_config(data_root=root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(root),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "profile": profile,
        "pit_items": items,
        "software": {
            "ashare_quant_lab": _safe_version("ashare-quant-lab"),
            "cnequity": _safe_version("cnequity"),
            "polars": pl.__version__,
        },
        "gates": [],
        "receipts": {},
        "training_dataset_unlocked": False,
    }

    # 1. Historical listing/delisting/ST and whole-market session coverage.
    try:
        universe = historical_universe_validity(config, start, end, profile=profile)
        universe_ok = bool(universe.get("universe_ready"))
        report["gates"].append(
            _gate(
                "historical_universe",
                universe_ok,
                {
                    "universe_ready": universe_ok,
                    "observed_window": universe.get("window"),
                    "supported_window": universe.get("supported_window"),
                    "checks": universe.get("checks"),
                    "blockers": universe.get("blockers", []),
                },
            )
        )
    except Exception as exc:  # retain a machine-readable FAIL report
        report["gates"].append(
            _gate("historical_universe", False, {"error": _error(exc)})
        )

    # The profile read provides the research symbol scope. If its receipt is
    # not replayable, a raw read may still collect diagnostic scope, but never
    # upgrades the universe gate or unlocks research data.
    symbols: list[str] = []
    try:
        universe_read = load_with_receipt(
            "daily_bars",
            start=start.isoformat(),
            end=end.isoformat(),
            profile=profile,
            strict_universe=True,
            require_replayable=True,
            data_root=root,
        )
        report["receipts"]["historical_universe"] = universe_read.receipt
        if not universe_read.frame.is_empty():
            symbols = sorted(universe_read.frame["symbol"].unique().to_list())
    except Exception as exc:
        report["gates"].append(
            _gate(
                "historical_universe_receipt",
                False,
                {"error": _error(exc), "reason": "strict/replayable profile read failed"},
            )
        )
        try:
            raw = load("daily_bars", start=start.isoformat(), end=end.isoformat(), data_root=root)
            if not raw.is_empty():
                symbols = sorted(raw["symbol"].unique().to_list())
        except Exception as raw_exc:
            report["scope_error"] = _error(raw_exc)

    report["scope"] = {"symbols": len(symbols)}
    if symbols:
        try:
            scope_bars = load(
                "daily_bars",
                start=start.isoformat(),
                end=end.isoformat(),
                symbols=symbols,
                data_root=root,
            )
            report["scope"].update(
                {
                    "daily_bar_rows": scope_bars.height,
                    "observed_start": str(scope_bars["trade_date"].min()),
                    "observed_end": str(scope_bars["trade_date"].max()),
                }
            )
        except Exception as exc:
            report["scope"]["read_error"] = _error(exc)

    # 2. HFQ factor coverage plus a revision-bound historical diagnostic.
    try:
        if not symbols:
            raise RuntimeError("No historical symbols available for factor audit")
        factor_diagnostic = publish_factor_diagnostics(config, symbols=symbols)
        adjusted = load_with_receipt(
            "daily_bars",
            start=start.isoformat(),
            end=end.isoformat(),
            symbols=symbols,
            profile=profile,
            strict_universe=True,
            adjust="hfq",
            strict_adj=True,
            require_replayable=True,
            data_root=root,
        )
        adjustment = adjusted.receipt.get("adjustment", {})
        factor_rows = adjusted.frame.height
        assessed = adjustment.get("diagnostics_assessed") is True
        related_events = int(adjustment.get("related_event_count", 0) or 0)
        missing_factor_rows = int(adjustment.get("missing_factor_rows", 0) or 0)
        hfq_ok = (
            not adjusted.frame.is_empty()
            and assessed
            and factor_diagnostic.get("assessed") is True
            and related_events == 0
            and missing_factor_rows == 0
            and bool(adjusted.receipt.get("replayable"))
        )
        report["receipts"]["hfq"] = adjusted.receipt
        report["gates"].append(
            _gate(
                "hfq_adjustment",
                hfq_ok,
                {
                    "rows": factor_rows,
                    "diagnostics_assessed": assessed,
                    "related_historical_events": related_events,
                    "missing_factor_rows": missing_factor_rows,
                    "factor_diagnostic": factor_diagnostic,
                    "historical_accuracy_is_not_inferred_from_adj_is_exact": True,
                },
            )
        )
    except Exception as exc:
        report["gates"].append(_gate("hfq_adjustment", False, {"error": _error(exc)}))

    # 3. Strict PIT fundamentals, checked against every in-scope symbol/item.
    try:
        if not symbols:
            raise RuntimeError("No historical symbols available for PIT audit")
        pit = load_with_receipt(
            "financial_statement_items",
            start=start.isoformat(),
            end=end.isoformat(),
            as_of=end.isoformat(),
            items=items,
            symbols=symbols,
            pit_mode="strict",
            require_replayable=True,
            data_root=root,
        )
        frame = pit.frame
        columns = set(frame.columns)
        required = {"pit_is_exact", "pit_quality", "symbol", "item"}
        quality_ok = (
            not frame.is_empty()
            and required.issubset(columns)
            and frame["pit_is_exact"].null_count() == 0
            and frame["pit_is_exact"].all()
            and frame.filter(pl.col("pit_quality") != "strict").is_empty()
        )
        missing_pairs: list[dict[str, str]] = []
        if quality_ok:
            observed_pairs = set(frame.select("symbol", "item").unique().iter_rows())
            missing_pairs = [
                {"symbol": symbol, "item": item}
                for symbol in symbols
                for item in items
                if (symbol, item) not in observed_pairs
            ]
        pit_ok = quality_ok and not missing_pairs and bool(pit.receipt.get("replayable"))
        report["receipts"]["pit"] = pit.receipt
        report["gates"].append(
            _gate(
                "point_in_time_fundamentals",
                pit_ok,
                {
                    "as_of": end.isoformat(),
                    "mode": "strict",
                    "items": items,
                    "rows": frame.height,
                    "symbols_with_rows": frame["symbol"].n_unique() if "symbol" in columns else 0,
                    "pit_quality_counts": (
                        frame.group_by("pit_quality").len().to_dicts()
                        if "pit_quality" in columns else []
                    ),
                    "missing_symbol_item_pairs": len(missing_pairs),
                    "missing_pair_sample": missing_pairs[:50],
                },
            )
        )
    except Exception as exc:
        report["gates"].append(
            _gate("point_in_time_fundamentals", False, {"error": _error(exc)})
        )

    # 4. CNEquity's actual-print coverage report, scoped to SH/SZ common stocks.
    try:
        evidence = traded_status_evidence(config, start=start, end=end).collect()
        shsz = evidence.filter(pl.col("asset_class") == "sh_sz_stock")
        eligible = shsz.filter(pl.col("exclusion").is_null())
        missing_rows = eligible.filter(~pl.col("has_status")).height
        unknown_st = eligible.filter(~pl.col("risk_warning_known")).height
        contradictions = eligible.filter(pl.col("contradiction")).height
        excluded = shsz.filter(pl.col("exclusion").is_not_null()).height
        trading_ok = (
            eligible.height > 0
            and missing_rows == 0
            and unknown_st == 0
            and contradictions == 0
            and excluded == 0
        )
        report["gates"].append(
            _gate(
                "trading_status",
                trading_ok,
                {
                    "scope": "SH/SZ stocks with positive-volume daily bars",
                    "traded_rows": eligible.height,
                    "missing_status_rows": missing_rows,
                    "unknown_risk_warning_rows": unknown_st,
                    "contradictory_rows": contradictions,
                    "excluded_or_unresolved_rows": excluded,
                    "by_month": (
                        eligible.group_by(pl.col("trade_date").dt.strftime("%Y-%m").alias("month"))
                        .len().sort("month").to_dicts()
                    ),
                    "note": "Suspensions are non-trading states; this gate checks status evidence for actual prints.",
                },
            )
        )
    except Exception as exc:
        report["gates"].append(_gate("trading_status", False, {"error": _error(exc)}))

    report["overall_status"] = (
        "PASS" if report["gates"] and all(g["status"] == "PASS" for g in report["gates"])
        else "FAIL"
    )
    # Deliberately remains false even on PASS: training integration must
    # consume and verify this report in a separate, explicit change.
    report["training_dataset_unlocked"] = False
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CNEquity 数据准入验收报告",
        "",
        f"- 总状态：**{report.get('overall_status', 'FAIL')}**",
        f"- 数据目录：`{report.get('data_root')}`",
        f"- 验收区间：{report.get('window', {}).get('start')} 至 {report.get('window', {}).get('end')}",
        f"- 股票池：`{report.get('profile')}`",
        f"- 股票数量：{report.get('scope', {}).get('symbols', 'N/A')}",
        "- 训练数据集开放：否（本工具只验收，不开放训练接口）",
        "",
        "| 门禁 | 状态 |",
        "|---|---|",
    ]
    for gate in report.get("gates", []):
        lines.append(f"| {gate['name']} | **{gate['status']}** |")
    lines.extend(["", "完整证据、覆盖统计和数据版本见同目录 JSON 报告。", ""])
    return "\n".join(lines)
