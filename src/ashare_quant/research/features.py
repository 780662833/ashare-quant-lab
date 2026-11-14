"""Point-in-time feature construction for the cross-sectional model.

This module is pure: it accepts canonical frames and never calls CNEquity,
reads a local lake, or performs model fitting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from math import isfinite
from zoneinfo import ZoneInfo
from typing import Any

import polars as pl

from ashare_quant.backtest.engine import weekly_rebalance_dates

PRICE_REQUIRED = {"instrument", "date", "adj_close", "adj_is_exact"}
EXECUTION_REQUIRED = {"instrument", "date", "volume", "amount"}
MEMBERSHIP_REQUIRED = {"instrument", "date"}
FUNDAMENTAL_REQUIRED = {
    "instrument", "item", "report_period", "available_at", "value",
    "pit_quality", "pit_is_exact",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class CrossSectionalFeatureConfig:
    """Feature windows are measured in official exchange sessions."""

    return_windows: tuple[int, ...] = (5, 20, 60, 120)
    volatility_window: int = 20
    activity_window: int = 20
    fundamental_items: tuple[str, ...] = ("roe", "net_profit")

    def __post_init__(self) -> None:
        if not self.return_windows or any(
            isinstance(window, bool) or not isinstance(window, int) or window < 1
            for window in self.return_windows
        ):
            raise ValueError("return_windows must contain positive integers")
        if len(set(self.return_windows)) != len(self.return_windows):
            raise ValueError("return_windows must be unique")
        for name, value in (
            ("volatility_window", self.volatility_window),
            ("activity_window", self.activity_window),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer >= 2")
        if not self.fundamental_items or len(set(self.fundamental_items)) != len(
            self.fundamental_items
        ):
            raise ValueError("fundamental_items must be non-empty and unique")


def _normalize_dates(values: list[Any]) -> list[str]:
    normalized = []
    for value in values:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError(f"Trading dates must use YYYY-MM-DD: {text!r}")
        normalized.append(text)
    if len(normalized) != len(set(normalized)):
        raise ValueError("Trading calendar contains duplicate sessions")
    if not normalized:
        raise ValueError("Trading calendar must not be empty")
    if normalized != sorted(normalized):
        raise ValueError("Trading calendar must be strictly chronological")
    return normalized


def _validate_frame(frame: pl.DataFrame, required: set[str], name: str) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(f"{name} must be a Polars DataFrame")
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")
    if frame.is_empty():
        raise ValueError(f"{name} must not be empty")
    result = frame.with_columns(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
    )
    if result.select(
        pl.any_horizontal(pl.col("instrument").is_null(), pl.col("date").is_null()).any()
    ).item():
        raise ValueError(f"{name} contains null keys")
    if result.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError(f"{name} contains duplicate instrument/date keys")
    return result


def _pit_values(
    fundamentals: pl.DataFrame,
    *,
    signal_dates: list[str],
    instruments: list[str],
    items: tuple[str, ...],
) -> pl.DataFrame:
    """Select latest strict, exact fact knowable by each signal close (16:00 CST)."""
    missing = FUNDAMENTAL_REQUIRED.difference(fundamentals.columns)
    if missing:
        raise ValueError(f"fundamentals missing columns: {sorted(missing)}")
    if fundamentals.is_empty():
        raise ValueError("fundamentals must not be empty")

    rows = fundamentals.select(sorted(FUNDAMENTAL_REQUIRED)).to_dicts()
    eligible: list[dict[str, Any]] = []
    seen_fact_keys: set[tuple[str, str, str, str]] = set()
    for row in rows:
        if row["item"] not in items or row["instrument"] not in instruments:
            continue
        if row["pit_quality"] != "strict" or row["pit_is_exact"] is not True:
            continue
        if row["report_period"] is None or not str(row["report_period"]).strip():
            raise ValueError("strict PIT report_period must be non-empty")
        available = row["available_at"]
        if isinstance(available, str):
            try:
                available = datetime.fromisoformat(available)
            except ValueError as exc:
                raise ValueError("available_at must be an ISO-8601 timestamp") from exc
        if not isinstance(available, datetime) or available.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        available = available.astimezone(SHANGHAI)

        # A duplicate source fact with the same availability timestamp is
        # ambiguous: choosing the first/last input row would make PIT features
        # depend on provider row order rather than a documented revision rule.
        fact_key = (
            str(row["instrument"]),
            str(row["item"]),
            str(row["report_period"]),
            available.isoformat(),
        )
        if fact_key in seen_fact_keys:
            raise ValueError("fundamentals contains duplicate strict PIT fact keys")
        seen_fact_keys.add(fact_key)

        if isinstance(row["value"], bool):
            raise ValueError("strict PIT value must be numeric, not boolean")
        try:
            value = float(row["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError("strict PIT value must be numeric") from exc
        if not isfinite(value):
            raise ValueError("strict PIT value must be finite")
        eligible.append({**row, "value": value, "_available": available})

    selected: list[dict[str, Any]] = []
    for signal_date in signal_dates:
        cutoff = datetime.combine(date.fromisoformat(signal_date), time(16, 0), SHANGHAI)
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for row in eligible:
            if row["_available"] > cutoff:
                continue
            key = (str(row["instrument"]), str(row["item"]))
            previous = latest.get(key)
            rank = (row["_available"], str(row["report_period"]))
            if previous is None or rank > (
                previous["_available"], str(previous["report_period"])
            ):
                latest[key] = row
        for instrument in instruments:
            output: dict[str, Any] = {"signal_date": signal_date, "instrument": instrument}
            for item in items:
                fact = latest.get((instrument, item))
                output[f"pit_{item}"] = None if fact is None else float(fact["value"])
            selected.append(output)

    schema: dict[str, pl.DataType] = {
        "signal_date": pl.String,
        "instrument": pl.String,
        **{f"pit_{item}": pl.Float64 for item in items},
    }
    return pl.DataFrame(selected, schema=schema)


def build_cross_sectional_features(
    *,
    adjusted_bars: pl.DataFrame,
    execution_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    fundamentals: pl.DataFrame,
    trading_dates: list[Any],
    config: CrossSectionalFeatureConfig | None = None,
) -> pl.DataFrame:
    """Build weekly as-of features; missing history stays null, never forward-filled.

    adjusted_bars supplies exact HFQ close for returns. execution_bars supplies
    raw volume/amount for activity features. Fundamentals must be strict and
    exact and are selected using publication availability <= signal close.
    """
    cfg = config or CrossSectionalFeatureConfig()
    sessions = _normalize_dates(trading_dates)
    prices = _validate_frame(adjusted_bars, PRICE_REQUIRED, "adjusted_bars")
    activity = _validate_frame(execution_bars, EXECUTION_REQUIRED, "execution_bars")
    members = _validate_frame(universe_membership, MEMBERSHIP_REQUIRED, "universe_membership")

    for frame, name in ((prices, "adjusted_bars"), (activity, "execution_bars"), (members, "universe_membership")):
        outside = set(frame["date"].to_list()).difference(sessions)
        if outside:
            raise ValueError(f"{name} contains dates outside trading calendar: {sorted(outside)[:3]}")

    for frame, name in ((prices, "adjusted_bars"), (activity, "execution_bars")):
        if frame.select(pl.any_horizontal(pl.col("instrument").is_null(), pl.col("date").is_null()).any()).item():
            raise ValueError(f"{name} contains null keys")

    try:
        prices = prices.with_columns(
            pl.col("adj_close").cast(pl.Float64, strict=True),
            pl.col("adj_is_exact").cast(pl.Boolean, strict=True),
        )
    except Exception as exc:
        raise ValueError("adjusted_bars must contain canonical numeric prices and exactness flags") from exc
    if prices.filter(
        pl.col("adj_close").is_null()
        | (~pl.col("adj_close").is_finite())
        | (pl.col("adj_close") <= 0)
        | pl.col("adj_is_exact").is_null()
        | (pl.col("adj_is_exact") != True)
    ).height:
        raise ValueError("HFQ adjusted close must be finite, positive and exact")
    if activity.filter(
        pl.col("volume").is_null()
        | pl.col("amount").is_null()
        | (~pl.col("volume").cast(pl.Float64).is_finite())
        | (~pl.col("amount").cast(pl.Float64).is_finite())
        | (pl.col("volume").cast(pl.Float64) < 0)
        | (pl.col("amount").cast(pl.Float64) < 0)
    ).height:
        raise ValueError("Raw volume and amount must be finite and non-negative")

    weekly_dates = weekly_rebalance_dates(sessions)
    missing_membership = sorted(set(weekly_dates).difference(members["date"].to_list()))
    if missing_membership:
        raise ValueError(f"Missing weekly point-in-time membership: {missing_membership}")

    instruments = sorted(set(members["instrument"].to_list()))
    # Explicit session grid makes shift(5) mean five exchange sessions. Missing
    # source rows remain null; no per-symbol row-count compression or fill occurs.
    grid = pl.DataFrame(
        {"instrument": [symbol for symbol in instruments for _ in sessions],
         "date": sessions * len(instruments)},
        schema={"instrument": pl.String, "date": pl.String},
    )
    panel = (
        grid.join(prices.select("instrument", "date", "adj_close"), on=["instrument", "date"], how="left")
        .join(activity.select("instrument", "date", "volume", "amount"), on=["instrument", "date"], how="left")
        .sort(["instrument", "date"])
    )
    panel = panel.with_columns(
        (pl.col("adj_close") / pl.col("adj_close").shift(1).over("instrument") - 1.0)
        .alias("_daily_return")
    )
    expressions = []
    for window in cfg.return_windows:
        expressions.append(
            (pl.col("adj_close") / pl.col("adj_close").shift(window).over("instrument") - 1.0)
            .alias(f"return_{window}")
        )
    expressions.extend([
        pl.col("_daily_return").rolling_std(window_size=cfg.volatility_window)
        .over("instrument").alias(f"volatility_{cfg.volatility_window}"),
        pl.col("amount").rolling_mean(window_size=cfg.activity_window)
        .over("instrument").alias(f"avg_amount_{cfg.activity_window}"),
        (
            pl.col("volume")
            / pl.col("volume").rolling_mean(window_size=cfg.activity_window).over("instrument")
        ).alias(f"volume_ratio_{cfg.activity_window}"),
    ])
    panel = panel.with_columns(expressions)
    market_features = panel.filter(pl.col("date").is_in(weekly_dates)).rename({"date": "signal_date"})
    market_features = market_features.join(
        members.rename({"date": "signal_date"}),
        on=["signal_date", "instrument"],
        how="inner",
    ).sort(["signal_date", "instrument"])

    pit = _pit_values(
        fundamentals,
        signal_dates=weekly_dates,
        instruments=instruments,
        items=cfg.fundamental_items,
    )
    result = market_features.join(pit, on=["signal_date", "instrument"], how="left")
    feature_columns = [
        *(f"return_{window}" for window in cfg.return_windows),
        f"volatility_{cfg.volatility_window}",
        f"avg_amount_{cfg.activity_window}",
        f"volume_ratio_{cfg.activity_window}",
        *(f"pit_{item}" for item in cfg.fundamental_items),
    ]
    return result.select("signal_date", "instrument", *feature_columns).sort(
        ["signal_date", "instrument"]
    )
