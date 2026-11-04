"""Normalize CNEquity v0.21 trading status and join independent price-limit evidence.

CNEquity v0.21.0 does not publish historical limit prices/lock flags in the
stable daily_bars or trading_status contracts. This module deliberately does
not derive them. A separately verified rule/evidence provider must supply
price_limit_evidence before an execution bundle can be assembled.
"""
from __future__ import annotations

import polars as pl

from ashare_quant.exceptions import ResearchBundleError

NATIVE_STATUS_COLUMNS = {
    "symbol", "trade_date", "is_trading", "status", "risk_warning",
}
LIMIT_EVIDENCE_COLUMNS = {
    "symbol", "trade_date", "is_limit_up_locked", "is_limit_down_locked",
    "limit_up", "limit_down",
}
CANONICAL_EXECUTION_COLUMNS = [
    "instrument", "date", "is_suspended", "is_limit_up_locked",
    "is_limit_down_locked", "limit_up", "limit_down", "risk_warning_known",
]
LEGACY_STATES = {"st", "*st"}
VALID_STATES = {"normal", "suspended", "delisted", *LEGACY_STATES}


def _validate_native_keys(frame: pl.DataFrame, name: str) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame) or frame.is_empty():
        raise ResearchBundleError(f"{name} must be a non-empty Polars DataFrame")
    required = {"symbol", "trade_date"}
    missing = required.difference(frame.columns)
    if missing:
        raise ResearchBundleError(f"{name} missing columns: {sorted(missing)}")
    normalized = frame.with_columns(
        pl.col("symbol").cast(pl.String, strict=True),
        pl.col("trade_date").cast(pl.String, strict=True),
    )
    if normalized.select(
        pl.any_horizontal(pl.col("symbol").is_null(), pl.col("trade_date").is_null()).any()
    ).item():
        raise ResearchBundleError(f"{name} contains null keys")
    if normalized.select(
        pl.struct(["symbol", "trade_date"]).is_duplicated().any()
    ).item():
        raise ResearchBundleError(f"{name} contains duplicate symbol/date keys")
    return normalized


def normalize_cnequity_trading_status(frame: pl.DataFrame) -> pl.DataFrame:
    """Map the exact CNEquity v0.21 status schema without erasing unknown ST.

    `status` has values normal/suspended/delisted. `risk_warning` is
    orthogonal and nullable. A null risk_warning is unknown, never False.
    Legacy status='st'/'*st' is retained as positive ST evidence for pre-migration
    rows. The returned `is_suspended` flag means non-tradable for the engine:
    both suspended and delisted states are blocked.
    """
    missing = NATIVE_STATUS_COLUMNS.difference(frame.columns)
    if missing:
        raise ResearchBundleError(
            f"CNEquity trading_status missing columns: {sorted(missing)}"
        )
    normalized = _validate_native_keys(frame, "CNEquity trading_status")
    try:
        normalized = normalized.with_columns(
            pl.col("is_trading").cast(pl.Boolean, strict=True),
            pl.col("risk_warning").cast(pl.Boolean, strict=True),
            pl.col("status").cast(pl.String, strict=True).str.strip_chars().str.to_lowercase(),
        )
    except Exception as exc:
        raise ResearchBundleError(f"Invalid CNEquity trading_status value types: {exc}") from exc

    if normalized.select(
        pl.any_horizontal(pl.col("is_trading").is_null(), pl.col("status").is_null()).any()
    ).item():
        raise ResearchBundleError("CNEquity trading_status has unknown trading state")
    invalid_states = normalized.filter(~pl.col("status").is_in(sorted(VALID_STATES)))
    if invalid_states.height:
        values = sorted(invalid_states["status"].unique().to_list())
        raise ResearchBundleError(f"CNEquity trading_status has unsupported status values: {values}")

    state_conflict = (
        (pl.col("is_trading") & pl.col("status").is_in(["suspended", "delisted"])) |
        (~pl.col("is_trading") & pl.col("status").is_in(["normal", "st", "*st"]))
    )
    if normalized.filter(state_conflict).height:
        raise ResearchBundleError("CNEquity is_trading/status fields contradict each other")

    legacy_st = pl.col("status").is_in(sorted(LEGACY_STATES))
    risk_warning = (
        pl.when(legacy_st)
        .then(pl.lit(True))
        .otherwise(pl.col("risk_warning"))
    )
    normalized = normalized.with_columns(
        risk_warning.alias("risk_warning"),
        (pl.col("risk_warning").is_not_null() | legacy_st).alias("risk_warning_known"),
        (
            (~pl.col("is_trading")) |
            pl.col("status").is_in(["suspended", "delisted"])
        ).alias("is_suspended"),
        pl.col("symbol").alias("instrument"),
        pl.col("trade_date").alias("date"),
    )
    keep = [
        "instrument", "date", "is_suspended", "risk_warning", "risk_warning_known",
        "is_trading", "status",
    ]
    for optional in ("source", "data_version", "fetched_at"):
        if optional in normalized.columns:
            keep.append(optional)
    return normalized.select(keep).sort(["date", "instrument"])


def combine_execution_evidence(
    raw_bars: pl.DataFrame,
    normalized_status: pl.DataFrame,
    price_limit_evidence: pl.DataFrame,
) -> pl.DataFrame:
    """Join native trading-state facts to separately verified price-limit facts.

    Every raw bar must have both sources on the same (instrument,date) key.
    Missing price-limit evidence is a hard error; no percentage limit or lock
    flag is inferred from close prices, board labels, or current market rules.
    """
    if not isinstance(raw_bars, pl.DataFrame) or not {"instrument", "date"}.issubset(raw_bars.columns):
        raise ResearchBundleError("raw_bars must contain instrument and date keys")
    raw_keys = raw_bars.select(
        pl.col("instrument").cast(pl.String, strict=True),
        pl.col("date").cast(pl.String, strict=True),
    )
    if raw_keys.is_empty():
        raise ResearchBundleError("raw_bars has no keys")
    if raw_keys.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ResearchBundleError("raw_bars contains duplicate instrument/date keys")

    if not {"instrument", "date", "is_suspended", "risk_warning_known"}.issubset(normalized_status.columns):
        raise ResearchBundleError("normalized_status is missing canonical trading-state fields")
    status = normalized_status.select(
        "instrument", "date", "is_suspended", "risk_warning_known"
    )
    status = _validate_native_keys(
        status.rename({"instrument": "symbol", "date": "trade_date"}),
        "normalized trading status",
    ).rename({"symbol": "instrument", "trade_date": "date"})

    missing_status = raw_keys.join(status.select("instrument", "date"), on=["instrument", "date"], how="anti")
    if missing_status.height:
        raise ResearchBundleError(
            f"CNEquity trading_status missing evidence for {missing_status.height} raw bars"
        )

    missing_limit = LIMIT_EVIDENCE_COLUMNS.difference(price_limit_evidence.columns)
    if missing_limit:
        raise ResearchBundleError(f"price_limit_evidence missing columns: {sorted(missing_limit)}")
    limits = price_limit_evidence.select(
        pl.col("symbol").cast(pl.String, strict=True).alias("instrument"),
        pl.col("trade_date").cast(pl.String, strict=True).alias("date"),
        "is_limit_up_locked", "is_limit_down_locked", "limit_up", "limit_down",
    )
    if limits.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ResearchBundleError("price_limit_evidence contains duplicate instrument/date keys")
    missing_limits = raw_keys.join(limits.select("instrument", "date"), on=["instrument", "date"], how="anti")
    if missing_limits.height:
        raise ResearchBundleError(
            f"Price-limit evidence missing for {missing_limits.height} raw bars"
        )

    try:
        status = status.with_columns(
            pl.col("is_suspended").cast(pl.Boolean, strict=True),
            pl.col("risk_warning_known").cast(pl.Boolean, strict=True),
        )
        limits = limits.with_columns(
            pl.col("is_limit_up_locked").cast(pl.Boolean, strict=True),
            pl.col("is_limit_down_locked").cast(pl.Boolean, strict=True),
            pl.col("limit_up").cast(pl.Float64, strict=True),
            pl.col("limit_down").cast(pl.Float64, strict=True),
        )
    except Exception as exc:
        raise ResearchBundleError(f"Invalid execution evidence types: {exc}") from exc

    combined = raw_keys.join(status, on=["instrument", "date"], how="left").join(
        limits, on=["instrument", "date"], how="left"
    )
    flags = [
        "is_suspended", "risk_warning_known", "is_limit_up_locked", "is_limit_down_locked",
    ]
    if combined.select(pl.any_horizontal([pl.col(c).is_null() for c in flags]).any()).item():
        raise ResearchBundleError("Execution evidence contains null state flags")
    if combined.filter(
        (pl.col("limit_up").is_not_null() & (
            (~pl.col("limit_up").is_finite()) | (pl.col("limit_up") <= 0)
        )) |
        (pl.col("limit_down").is_not_null() & (
            (~pl.col("limit_down").is_finite()) | (pl.col("limit_down") <= 0)
        ))
    ).height:
        raise ResearchBundleError("Execution evidence contains invalid price limits")
    if combined.filter(pl.col("is_limit_up_locked") & pl.col("limit_up").is_null()).height:
        raise ResearchBundleError("Limit-up lock flag lacks a limit-up price")
    if combined.filter(pl.col("is_limit_down_locked") & pl.col("limit_down").is_null()).height:
        raise ResearchBundleError("Limit-down lock flag lacks a limit-down price")
    return combined.select(CANONICAL_EXECUTION_COLUMNS).sort(["date", "instrument"])
