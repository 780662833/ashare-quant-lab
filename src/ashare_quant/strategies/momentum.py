"""Point-in-time weekly momentum benchmark strategy.

Momentum uses adjusted close prices for research signals only. Execution must
continue to use raw OHLC through the shared backtest engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from ashare_quant.backtest.engine import equal_weight_targets, weekly_rebalance_dates

PRICE_COLUMNS = {"instrument", "date", "adj_close"}
MEMBERSHIP_COLUMNS = {"instrument", "date"}


@dataclass(frozen=True, slots=True)
class MomentumConfig:
    """Weekly 6-to-1 style momentum benchmark defaults."""

    top_n: int = 20
    lookback_sessions: int = 126
    skip_recent_sessions: int = 21

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        if self.lookback_sessions < 2:
            raise ValueError("lookback_sessions must be at least 2")
        if self.skip_recent_sessions < 0:
            raise ValueError("skip_recent_sessions must be non-negative")
        if self.skip_recent_sessions >= self.lookback_sessions:
            raise ValueError("skip_recent_sessions must be smaller than lookback_sessions")


def _iso_dates(values: list[Any], *, name: str) -> list[str]:
    result: list[str] = []
    for value in values:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{name} contains a non-ISO date: {text!r}") from exc
        if parsed.isoformat() != text:
            raise ValueError(f"{name} dates must use YYYY-MM-DD: {text!r}")
        result.append(text)
    return result


def _validate_inputs(
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_dates: list[Any],
) -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
    missing_prices = PRICE_COLUMNS.difference(adjusted_bars.columns)
    if missing_prices:
        raise ValueError(f"Adjusted bars missing columns: {sorted(missing_prices)}")
    missing_membership = MEMBERSHIP_COLUMNS.difference(universe_membership.columns)
    if missing_membership:
        raise ValueError(f"Universe membership missing columns: {sorted(missing_membership)}")
    if adjusted_bars.is_empty():
        raise ValueError("adjusted_bars must not be empty")
    if universe_membership.is_empty():
        raise ValueError("universe_membership must not be empty")

    raw_sessions = _iso_dates(trading_dates, name="trading_dates")
    if len(raw_sessions) != len(set(raw_sessions)):
        raise ValueError("trading_dates must contain unique sessions")
    sessions = sorted(raw_sessions)
    if len(sessions) < 2:
        raise ValueError("trading_dates must contain at least two unique sessions")
    session_set = set(sessions)

    prices = adjusted_bars.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
        pl.col("adj_close").cast(pl.Float64),
    )
    membership = universe_membership.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
    )
    price_dates = set(prices["date"].to_list())
    member_dates = set(membership["date"].to_list())
    if not price_dates.issubset(session_set):
        raise ValueError("Adjusted bars contain dates outside the supplied trading calendar")
    if not member_dates.issubset(session_set):
        raise ValueError("Universe membership contains dates outside the supplied trading calendar")
    if prices.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("Adjusted bars contain duplicate instrument/date keys")
    if membership.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("Universe membership contains duplicate instrument/date keys")
    if prices.select(
        pl.any_horizontal(
            pl.col("instrument").is_null(),
            pl.col("date").is_null(),
            pl.col("adj_close").is_null(),
        ).any()
    ).item():
        raise ValueError("Adjusted bars contain null instrument/date/adj_close values")
    if prices.filter(
        (~pl.col("adj_close").is_finite()) | (pl.col("adj_close") <= 0)
    ).height:
        raise ValueError("Adjusted close prices must be finite and positive")
    if membership.select(
        pl.any_horizontal(pl.col("instrument").is_null(), pl.col("date").is_null()).any()
    ).item():
        raise ValueError("Universe membership contains null keys")
    return prices.sort(["date", "instrument"]), membership.sort(["date", "instrument"]), sessions


def momentum_scores(
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_dates: list[Any],
    config: MomentumConfig | None = None,
) -> pl.DataFrame:
    """Compute weekly cross-sectional momentum scores using only known history.

    For signal date t, score = adjusted_close(t - skip) /
    adjusted_close(t - lookback) - 1. The default 126/21 session window
    excludes the most recent 21 sessions. Membership is taken as-of t.
    A security missing either exact endpoint price is excluded; prices are
    never forward-filled or replaced with raw close.
    """
    cfg = config or MomentumConfig()
    prices, membership, sessions = _validate_inputs(
        adjusted_bars, universe_membership, trading_dates
    )
    weekly_dates = weekly_rebalance_dates(sessions)
    membership_dates = set(membership["date"].to_list())
    missing_membership_dates = sorted(set(weekly_dates).difference(membership_dates))
    if missing_membership_dates:
        raise ValueError(
            "Point-in-time universe membership missing weekly signal dates: "
            f"{missing_membership_dates}"
        )

    rows: list[dict[str, Any]] = []
    index_by_date = {session: index for index, session in enumerate(sessions)}
    for signal_date in weekly_dates:
        signal_index = index_by_date[signal_date]
        start_index = signal_index - cfg.lookback_sessions
        end_index = signal_index - cfg.skip_recent_sessions
        if start_index < 0 or end_index < 0:
            continue

        start_date = sessions[start_index]
        end_date = sessions[end_index]
        current_members = membership.filter(pl.col("date") == signal_date).select("instrument")
        if current_members.is_empty():
            raise ValueError(f"No point-in-time universe members supplied for {signal_date}")

        start_prices = (
            prices.filter(pl.col("date") == start_date)
            .select("instrument", pl.col("adj_close").alias("start_price"))
        )
        end_prices = (
            prices.filter(pl.col("date") == end_date)
            .select("instrument", pl.col("adj_close").alias("end_price"))
        )
        candidates = (
            current_members
            .join(start_prices, on="instrument", how="inner")
            .join(end_prices, on="instrument", how="inner")
            .with_columns(
                (pl.col("end_price") / pl.col("start_price") - 1.0).alias("score")
            )
            .filter(pl.col("score").is_finite())
            .sort(["score", "instrument"], descending=[True, False])
        )
        rows.extend(
            {
                "signal_date": signal_date,
                "instrument": row["instrument"],
                "score": float(row["score"]),
                "start_date": start_date,
                "end_date": end_date,
            }
            for row in candidates.select(
                "instrument", "score"
            ).to_dicts()
        )

    return pl.DataFrame(
        rows,
        schema={
            "signal_date": pl.String,
            "instrument": pl.String,
            "score": pl.Float64,
            "start_date": pl.String,
            "end_date": pl.String,
        },
    )


def weekly_momentum_targets(
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_dates: list[Any],
    config: MomentumConfig | None = None,
) -> pl.DataFrame:
    """Produce the benchmark's weekly top-N equal-weight target weights."""
    cfg = config or MomentumConfig()
    scores = momentum_scores(adjusted_bars, universe_membership, trading_dates, cfg)
    if scores.is_empty():
        return pl.DataFrame(
            schema={
                "signal_date": pl.String,
                "instrument": pl.String,
                "weight": pl.Float64,
            }
        )
    return equal_weight_targets(
        scores.select("signal_date", "instrument", "score"),
        top_n=cfg.top_n,
    )
