"""Four-gate, fail-closed acceptance of a local CNEquity research lake.

This module deliberately does not create or expose a training dataset. It emits
an auditable report; downstream training must separately require overall PASS.
"""
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


def run_acceptance(
    *,
    data_root: str | Path,
    start_date: str,
    end_date: str,
    pit_items: list[str] | None = None,
    profile: str = PROFILE,
) -> dict[str, Any]:
    """Run historical universe, HFQ, strict PIT and trading-state gates.

    All reads are pinned with CNEquity receipts. Any missing evidence, empty
    result, unassessed adjustment diagnosis, or incomplete status coverage
    fails closed.
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
    from cnequity.quality.trading_status_coverage import trading_state_coverage_report

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

    # 1. Historical membership: CNEquity's full validity contract checks
    # interior market-session holes, ST receipts and delisted survivorship.
    universe = historical_universe_validity(
        config, start, end, profile=profile
    )
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

    # Fetch the strict profile universe once; it is also the symbol scope for
    # factor diagnostics and PIT coverage.
    universe_read = load_with_receipt(
        "daily_bars",
        start=start.isoformat(),
        end=end.isoformat(),
        profile=profile,
        strict_universe=True,
        require_replayable=True,
        data_root=root,
    )
    universe_bars = universe_read.frame
    if universe_bars.is_empty():
        raise RuntimeError("Strict profile returned no daily bars; acceptance cannot continue")
    symbols = sorted(universe_bars["symbol"].unique().to_list())
    report["receipts"]["historical_universe"] = universe_read.receipt
    report["scope"] = {
        "symbols": len(symbols),
        "daily_bar_rows": universe_bars.height,
        "observed_start": str(universe_bars["trade_date"].min()),
        "observed_end": str(universe_bars["trade_date"].max()),
    }

    # 2. HFQ: coverage and historical economic accuracy are separate.
    # CNEquity's version-bound diagnostic must be assessed for this symbol
    # scope; an unassessed diagnostic is a FAIL, never an implicit PASS.
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
    adjustment_assessed = adjustment.get("diagnostics_assessed") is True
    related_events = int(adjustment.get("related_event_count", 0) or 0)
    missing_factor_rows = int(adjustment.get("missing_factor_rows", 0) or 0)
    hfq_ok = (
        not adjusted.frame.is_empty()
        and adjustment_assessed
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
                "diagnostics_assessed": adjustment_assessed,
                "related_historical_events": related_events,
                "missing_factor_rows": missing_factor_rows,
                "factor_diagnostic": factor_diagnostic,
                "historical_accuracy_is_not_inferred_from_adj_is_exact": True,
            },
        )
    )

    # 3. Strict PIT fundamentals: use documented item codes, strict as-of
    # visibility, and require each profile symbol to have each requested item.
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
    pit_frame = pit.frame
    pit_columns = set(pit_frame.columns)
    pit_quality_ok = (
        not pit_frame.is_empty()
        and {"pit_is_exact", "pit_quality", "symbol", "item"}.issubset(pit_columns)
        and pit_frame["pit_is_exact"].null_count() == 0
        and pit_frame["pit_is_exact"].all()
        and pit_frame.filter(pl.col("pit_quality") != "strict").is_empty()
    )
    missing_symbol_items: list[dict[str, str]] = []
    if pit_quality_ok:
        observed_pairs = set(
            pit_frame.select("symbol", "item").unique().iter_rows()
        )
        missing_symbol_items = [
            {"symbol": symbol, "item": item}
            for symbol in symbols
            for item in items
            if (symbol, item) not in observed_pairs
        ]
    pit_ok = (
        pit_quality_ok
        and not missing_symbol_items
        and bool(pit.receipt.get("replayable"))
    )
    report["receipts"]["pit"] = pit.receipt
    report["gates"].append(
        _gate(
            "point_in_time_fundamentals",
            pit_ok,
            {
                "as_of": end.isoformat(),
                "mode": "strict",
                "items": items,
                "rows": pit_frame.height,
                "symbols_with_rows": (
                    pit_frame["symbol"].n_unique() if "symbol" in pit_columns else 0
                ),
                "pit_quality_counts": (
                    pit_frame.group_by("pit_quality").len().to_dicts()
                    if "pit_quality" in pit_columns else []
                ),
                "missing_symbol_item_pairs": len(missing_symbol_items),
                "missing_pair_sample": missing_symbol_items[:50],
            },
        )
    )

    # 4. Trading state: CNEquity's own positive-volume key audit measures
    # status presence, ST-known coverage and contradictions over the window.
    trading = trading_state_coverage_report(config, start=start, end=end)
    trading_ok = (
        int(trading.get("traded_rows", 0) or 0) > 0
        and int(trading.get("missing_rows", 0) or 0) == 0
        and int(trading.get("risk_warning_unknown_rows", 0) or 0) == 0
        and int(trading.get("contradictory_rows", 0) or 0) == 0
        and int(trading.get("excluded_rows", 0) or 0) == 0
    )
    report["gates"].append(
        _gate("trading_status", trading_ok, trading)
    )

    report["overall_status"] = (
        "PASS" if all(gate["status"] == "PASS" for gate in report["gates"]) else "FAIL"
    )
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
        f"- 训练数据集开放：否（需单独实现受门禁保护的构建流程）",
        "",
        "| 门禁 | 状态 |",
        "|---|---|",
    ]
    for gate in report.get("gates", []):
        lines.append(f"| {gate['name']} | **{gate['status']}** |")
    lines.extend(["", "完整证据、覆盖统计和数据版本见同目录 JSON 报告。", ""])
    return "\n".join(lines)
