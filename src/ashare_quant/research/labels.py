"""Forward-return labels with explicit maturity dates for temporal purging."""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from ashare_quant.backtest.engine import weekly_rebalance_dates


def build_forward_return_labels(
    *,
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_dates: list[Any],
    horizon_sessions: int = 5,
) -> pl.DataFrame:
    """Label signal-date members with exact future HFQ close-to-close returns.

    A label is emitted only when the exact t and t+horizon session prices
    exist. The label_end_date is mandatory for purge/embargo checks. Universe
    membership is frozen at the signal date; future membership is never used.
    """
    if isinstance(horizon_sessions, bool) or not isinstance(horizon_sessions, int) or horizon_sessions < 1:
        raise ValueError("horizon_sessions must be a positive integer")
    required_prices = {"instrument", "date", "adj_close"}
    required_members = {"instrument", "date"}
    if not required_prices.issubset(adjusted_bars.columns):
        raise ValueError(f"adjusted_bars missing columns: {sorted(required_prices - set(adjusted_bars.columns))}")
    if not required_members.issubset(universe_membership.columns):
        raise ValueError(f"universe_membership missing columns: {sorted(required_members - set(universe_membership.columns))}")
    if adjusted_bars.is_empty() or universe_membership.is_empty():
        raise ValueError("adjusted_bars and universe_membership must not be empty")

    sessions = []
    for value in trading_dates:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError(f"Trading dates must use YYYY-MM-DD: {text!r}")
        sessions.append(text)
    if len(sessions) != len(set(sessions)):
        raise ValueError("Trading calendar contains duplicate sessions")
    sessions = sorted(sessions)
    if not sessions:
        raise ValueError("Trading calendar must not be empty")

    prices = adjusted_bars.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
        pl.col("adj_close").cast(pl.Float64),
    )
    members = universe_membership.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
    )
    if prices.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("adjusted_bars contains duplicate instrument/date keys")
    if members.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("universe_membership contains duplicate instrument/date keys")
    if prices.filter(
        pl.col("adj_close").is_null() | (~pl.col("adj_close").is_finite()) | (pl.col("adj_close") <= 0)
    ).height:
        raise ValueError("HFQ adjusted close must be finite and positive")

    weekly_dates = weekly_rebalance_dates(sessions)
    index = {session: i for i, session in enumerate(sessions)}
    rows: list[dict[str, Any]] = []
    for signal_date in weekly_dates:
        end_index = index[signal_date] + horizon_sessions
        if end_index >= len(sessions):
            continue
        end_date = sessions[end_index]
        signal_members = members.filter(pl.col("date") == signal_date).select("instrument")
        start_prices = prices.filter(pl.col("date") == signal_date).select(
            "instrument", pl.col("adj_close").alias("start_price")
        )
        end_prices = prices.filter(pl.col("date") == end_date).select(
            "instrument", pl.col("adj_close").alias("end_price")
        )
        matched = (
            signal_members.join(start_prices, on="instrument", how="inner")
            .join(end_prices, on="instrument", how="inner")
            .with_columns((pl.col("end_price") / pl.col("start_price") - 1.0).alias("forward_return"))
        )
        rows.extend(
            {
                "signal_date": signal_date,
                "instrument": row["instrument"],
                "label_end_date": end_date,
                "forward_return": float(row["forward_return"]),
            }
            for row in matched.select("instrument", "forward_return").to_dicts()
            if row["forward_return"] is not None
        )

    return pl.DataFrame(
        rows,
        schema={
            "signal_date": pl.String,
            "instrument": pl.String,
            "label_end_date": pl.String,
            "forward_return": pl.Float64,
        },
    )
