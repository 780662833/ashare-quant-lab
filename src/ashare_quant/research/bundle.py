"""Canonical, fail-closed inputs for A-share research runs.

This module does not call CNEquity. Provider-specific data is normalized by
adapters first, then validated and joined here. It never constructs a training
dataset and never unlocks model training.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest.engine import weekly_rebalance_dates

RAW_COLUMNS = {
    "instrument", "date", "open", "high", "low", "close", "volume", "amount",
}
STATUS_COLUMNS = {
    "instrument", "date", "is_suspended", "is_limit_up_locked",
    "is_limit_down_locked", "limit_up", "limit_down", "risk_warning_known",
}
ADJUSTED_COLUMNS = {"instrument", "date", "adj_close", "adj_is_exact"}
UNIVERSE_COLUMNS = {"instrument", "date"}
REQUIRED_BENCHMARK_GATES = {
    "historical_universe", "hfq_adjustment", "trading_status",
}
ENGINE_COLUMNS = [
    "instrument", "date", "open", "high", "low", "close", "volume", "amount",
    "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "limit_up", "limit_down",
]


class ResearchBundleError(ValueError):
    """Raised when research inputs are incomplete, contradictory, or untraceable."""


@dataclass(frozen=True, slots=True)
class ResearchDataBundle:
    """Validated and provenance-bound data passed to strategies and the engine."""

    execution_bars: pl.DataFrame
    adjusted_bars: pl.DataFrame
    universe_membership: pl.DataFrame
    trading_dates: list[str]
    provenance: dict[str, Any]


def _iso_date(value: Any, *, field: str) -> str:
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ResearchBundleError(f"{field} contains invalid ISO date {text!r}") from exc
    if parsed.isoformat() != text:
        raise ResearchBundleError(f"{field} must use YYYY-MM-DD, got {text!r}")
    return text


def _date_window(start_date: str, end_date: str) -> tuple[str, str]:
    start = _iso_date(start_date, field="start_date")
    end = _iso_date(end_date, field="end_date")
    if start > end:
        raise ResearchBundleError("start_date must be on or before end_date")
    return start, end


def _keyed_frame(
    frame: pl.DataFrame,
    *,
    name: str,
    required_columns: set[str],
    start_date: str,
    end_date: str,
) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame):
        raise ResearchBundleError(f"{name} must be a Polars DataFrame")
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ResearchBundleError(f"{name} missing columns: {sorted(missing)}")
    if frame.is_empty():
        raise ResearchBundleError(f"{name} must not be empty")

    normalized = frame.with_columns(
        pl.col("instrument").cast(pl.String, strict=True),
        pl.col("date").cast(pl.String, strict=True),
    )
    if normalized.select(
        pl.any_horizontal(pl.col("instrument").is_null(), pl.col("date").is_null()).any()
    ).item():
        raise ResearchBundleError(f"{name} contains null instrument/date keys")
    if normalized.select(
        pl.struct(["instrument", "date"]).is_duplicated().any()
    ).item():
        raise ResearchBundleError(f"{name} contains duplicate instrument/date keys")

    values = normalized["date"].unique().to_list()
    for value in values:
        normalized_date = _iso_date(value, field=f"{name}.date")
        if not start_date <= normalized_date <= end_date:
            raise ResearchBundleError(
                f"{name} contains date outside requested window: {normalized_date}"
            )
    return normalized


def _require_gates(
    report: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    universe_profile: str,
) -> dict[str, str]:
    if not isinstance(report, Mapping):
        raise ResearchBundleError("acceptance_report must be a mapping")
    if report.get("window") != {"start": start_date, "end": end_date}:
        raise ResearchBundleError("Acceptance report window does not match bundle window")
    if report.get("profile") != universe_profile:
        raise ResearchBundleError("Acceptance report universe profile does not match")
    if report.get("training_dataset_unlocked") is not False:
        raise ResearchBundleError(
            "Research bundle must not unlock training; expected training_dataset_unlocked=false"
        )

    gates = report.get("gates")
    if not isinstance(gates, list):
        raise ResearchBundleError("Acceptance report is missing its gates list")
    gate_map: dict[str, str] = {}
    for gate in gates:
        if not isinstance(gate, Mapping) or not gate.get("name"):
            raise ResearchBundleError("Acceptance report contains a malformed gate")
        name = str(gate["name"])
        if name in gate_map:
            raise ResearchBundleError(f"Acceptance report has duplicate gate {name!r}")
        gate_map[name] = str(gate.get("status", "FAIL")).upper()

    failed = sorted(
        name for name in REQUIRED_BENCHMARK_GATES if gate_map.get(name) != "PASS"
    )
    if failed:
        raise ResearchBundleError(
            "Benchmark data admission failed these required gates: " + ", ".join(failed)
        )
    return {name: gate_map[name] for name in sorted(REQUIRED_BENCHMARK_GATES)}


def build_research_data_bundle(
    *,
    raw_bars: pl.DataFrame,
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_status: pl.DataFrame,
    trading_dates: list[Any],
    acceptance_report: Mapping[str, Any],
    acceptance_report_id: str,
    source_revisions: Mapping[str, str],
    start_date: str,
    end_date: str,
    universe_profile: str,
) -> ResearchDataBundle:
    """Validate and assemble the canonical benchmark research bundle.

    Momentum admission requires historical-universe, HFQ, and trading-status
    gates to PASS. Fundamental PIT is not consumed by this price-only
    benchmark; a future model-training builder must enforce all four gates
    independently and is deliberately not implemented here.

    Missing status, adjusted price, weekly membership, or calendar coverage
    is a hard failure. No missing execution state or adjusted value is inferred.
    """
    start, end = _date_window(start_date, end_date)
    if not universe_profile:
        raise ResearchBundleError("universe_profile must be non-empty")
    if not acceptance_report_id or not acceptance_report_id.strip():
        raise ResearchBundleError("acceptance_report_id must be non-empty")
    if not isinstance(source_revisions, Mapping) or not source_revisions:
        raise ResearchBundleError("source_revisions must be a non-empty mapping")
    revisions = {str(k): str(v) for k, v in source_revisions.items()}
    if any(not key.strip() or not value.strip() for key, value in revisions.items()):
        raise ResearchBundleError("source_revisions keys and values must be non-empty")

    required_gate_status = _require_gates(
        acceptance_report,
        start_date=start,
        end_date=end,
        universe_profile=universe_profile,
    )

    normalized_sessions = [_iso_date(value, field="trading_dates") for value in trading_dates]
    if len(normalized_sessions) != len(set(normalized_sessions)):
        raise ResearchBundleError("trading_dates must contain unique sessions")
    sessions = sorted(normalized_sessions)
    if len(sessions) < 2:
        raise ResearchBundleError("trading_dates must contain at least two sessions")
    if sessions[0] < start or sessions[-1] > end:
        raise ResearchBundleError("trading_dates fall outside the requested window")
    weekly_dates = weekly_rebalance_dates(sessions)

    raw = _keyed_frame(
        raw_bars, name="raw_bars", required_columns=RAW_COLUMNS,
        start_date=start, end_date=end,
    )
    adjusted = _keyed_frame(
        adjusted_bars, name="adjusted_bars", required_columns=ADJUSTED_COLUMNS,
        start_date=start, end_date=end,
    )
    membership = _keyed_frame(
        universe_membership, name="universe_membership",
        required_columns=UNIVERSE_COLUMNS, start_date=start, end_date=end,
    )
    status = _keyed_frame(
        trading_status, name="trading_status", required_columns=STATUS_COLUMNS,
        start_date=start, end_date=end,
    )

    raw_numeric = ["open", "high", "low", "close", "volume", "amount"]
    try:
        raw = raw.with_columns([pl.col(column).cast(pl.Float64, strict=True) for column in raw_numeric])
        adjusted = adjusted.with_columns(
            pl.col("adj_close").cast(pl.Float64, strict=True),
            pl.col("adj_is_exact").cast(pl.Boolean, strict=True),
        )
        status = status.with_columns(
            [pl.col(column).cast(pl.Boolean, strict=True) for column in (
                "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "risk_warning_known",
            )],
            pl.col("limit_up").cast(pl.Float64, strict=True),
            pl.col("limit_down").cast(pl.Float64, strict=True),
        )
    except Exception as exc:
        raise ResearchBundleError(f"Canonical inputs contain non-canonical value types: {exc}") from exc

    if raw.select(pl.any_horizontal([pl.col(c).is_null() for c in raw_numeric]).any()).item():
        raise ResearchBundleError("raw_bars contains null OHLCV values")
    invalid_raw = (
        (pl.col("open") <= 0) | (pl.col("high") <= 0) |
        (pl.col("low") <= 0) | (pl.col("close") <= 0) |
        (pl.col("high") < pl.max_horizontal("open", "close", "low")) |
        (pl.col("low") > pl.min_horizontal("open", "close", "high")) |
        (pl.col("volume") < 0) | (pl.col("amount") < 0) |
        pl.any_horizontal([~pl.col(c).is_finite() for c in raw_numeric])
    )
    if raw.select(invalid_raw.any()).item():
        raise ResearchBundleError("raw_bars contains invalid OHLCV values")

    if adjusted.select(
        pl.any_horizontal(pl.col("adj_close").is_null(), pl.col("adj_is_exact").is_null()).any()
    ).item():
        raise ResearchBundleError("adjusted_bars contains null adjusted price/exactness")
    if adjusted.filter(
        (~pl.col("adj_close").is_finite()) |
        (pl.col("adj_close") <= 0) |
        (pl.col("adj_is_exact") != True)
    ).height:
        raise ResearchBundleError("adjusted_bars contains non-positive or non-exact HFQ values")

    status_flags = [
        "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "risk_warning_known",
    ]
    if status.select(pl.any_horizontal([pl.col(c).is_null() for c in status_flags]).any()).item():
        raise ResearchBundleError("trading_status contains unknown/null state flags")
    if status.filter(~pl.col("risk_warning_known")).height:
        raise ResearchBundleError("trading_status contains unknown risk-warning state")
    if status.filter(
        (pl.col("limit_up").is_not_null() & (
            (~pl.col("limit_up").is_finite()) | (pl.col("limit_up") <= 0)
        )) |
        (pl.col("limit_down").is_not_null() & (
            (~pl.col("limit_down").is_finite()) | (pl.col("limit_down") <= 0)
        ))
    ).height:
        raise ResearchBundleError("trading_status contains invalid limit prices")

    raw_dates = set(raw["date"].to_list())
    if raw_dates != set(sessions):
        missing = sorted(set(sessions).difference(raw_dates))
        unexpected = sorted(raw_dates.difference(sessions))
        raise ResearchBundleError(
            f"raw_bars calendar coverage mismatch; missing={missing}, unexpected={unexpected}"
        )

    raw_keys = raw.select("instrument", "date")
    adjusted_keys = adjusted.select("instrument", "date")
    missing_adjusted = raw_keys.join(adjusted_keys, on=["instrument", "date"], how="anti")
    extra_adjusted = adjusted_keys.join(raw_keys, on=["instrument", "date"], how="anti")
    if missing_adjusted.height or extra_adjusted.height:
        raise ResearchBundleError(
            "raw/adjusted key coverage differs; "
            f"missing_adjusted={missing_adjusted.height}, extra_adjusted={extra_adjusted.height}"
        )

    raw_instruments = set(raw["instrument"].to_list())
    unknown_members = sorted(set(membership["instrument"].to_list()).difference(raw_instruments))
    if unknown_members:
        raise ResearchBundleError(
            f"universe_membership contains instruments outside raw market scope: {unknown_members[:20]}"
        )
    missing_weekly_membership = sorted(
        set(weekly_dates).difference(set(membership["date"].to_list()))
    )
    if missing_weekly_membership:
        raise ResearchBundleError(
            "Point-in-time membership missing weekly signal dates: "
            f"{missing_weekly_membership}"
        )

    raw_keys_status = raw_keys.join(
        status.select("instrument", "date"), on=["instrument", "date"], how="anti"
    )
    if raw_keys_status.height:
        raise ResearchBundleError(
            f"trading_status missing evidence for {raw_keys_status.height} raw market rows"
        )
    joined = raw.join(status, on=["instrument", "date"], how="left")
    if joined.select(pl.any_horizontal([pl.col(c).is_null() for c in status_flags]).any()).item():
        raise ResearchBundleError("Joined execution rows contain missing trading-state evidence")
    if joined.filter(pl.col("is_suspended") & (pl.col("volume") > 0)).height:
        raise ResearchBundleError("Trading-status contradiction: suspended security has positive volume")

    execution_bars = joined.select(ENGINE_COLUMNS).sort(["date", "instrument"])
    adjusted_out = adjusted.select(
        "instrument", "date", "adj_close", "adj_is_exact"
    ).sort(["date", "instrument"])
    membership_out = membership.select("instrument", "date").sort(["date", "instrument"])

    provenance = {
        "acceptance_report_id": acceptance_report_id,
        "acceptance_report_created_at": acceptance_report.get("created_at"),
        "acceptance_report_window": {"start": start, "end": end},
        "universe_profile": universe_profile,
        "required_benchmark_gates": required_gate_status,
        "pit_fundamentals_used": False,
        "training_dataset_unlocked": False,
        "source_revisions": revisions,
        "source_receipts": acceptance_report.get("receipts", {}),
        "scope": {
            "start_date": start,
            "end_date": end,
            "trading_sessions": len(sessions),
            "raw_bar_rows": raw.height,
            "adjusted_bar_rows": adjusted_out.height,
            "universe_membership_rows": membership_out.height,
            "instruments": len(raw_instruments),
            "weekly_signal_dates": weekly_dates,
        },
    }
    return ResearchDataBundle(
        execution_bars=execution_bars,
        adjusted_bars=adjusted_out,
        universe_membership=membership_out,
        trading_dates=sessions,
        provenance=provenance,
    )
