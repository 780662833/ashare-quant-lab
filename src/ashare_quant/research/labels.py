"""Forward-return labels aligned to the strategy's next-open execution cycle.

Each weekly signal is measured from the adjusted open of its first executable
session to the adjusted open of the first executable session after the next
weekly signal. The recorded maturity date is therefore the actual exit
execution date, including holiday-shortened weeks.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from ashare_quant.backtest.engine import weekly_rebalance_dates


def build_forward_return_labels(
    *,
    adjusted_bars: pl.DataFrame,
    execution_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    trading_dates: list[Any],
) -> pl.DataFrame:
    """Build exact execution-aligned, weekly open-to-open HFQ labels.

    Signals are formed after close(signal_date). Entry is the next exchange
    session's open. Exit is the next exchange session's open after the next
    weekly signal, matching when the backtest can first replace the holding.

    The canonical bundle carries HFQ adjusted close plus raw OHLC. The
    same-session adjustment multiplier is derived as adj_close / raw_close
    and applied to raw open. No price is forward-filled. A label is emitted
    only when both adjusted entry and exit opens exist. Membership is frozen
    at signal_date; future membership is never consulted.

    label_end_date is the exit execution date and must mature strictly before
    the training cutoff. label_entry_date records the first executable session.
    """
    required_prices = {"instrument", "date", "adj_close", "adj_is_exact"}
    required_execution = {"instrument", "date", "open", "close"}
    required_members = {"instrument", "date"}
    for name, frame, required in (
        ("adjusted_bars", adjusted_bars, required_prices),
        ("execution_bars", execution_bars, required_execution),
        ("universe_membership", universe_membership, required_members),
    ):
        if not isinstance(frame, pl.DataFrame):
            raise TypeError(f"{name} must be a Polars DataFrame")
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")
        if frame.is_empty():
            raise ValueError(f"{name} must not be empty")

    sessions: list[str] = []
    for value in trading_dates:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError(f"Trading dates must use YYYY-MM-DD: {text!r}")
        sessions.append(text)
    if len(sessions) != len(set(sessions)):
        raise ValueError("Trading calendar contains duplicate sessions")
    if sessions != sorted(sessions):
        raise ValueError("Trading calendar must be strictly chronological")
    if not sessions:
        raise ValueError("Trading calendar must not be empty")

    prices = adjusted_bars.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
        pl.col("adj_close").cast(pl.Float64),
        pl.col("adj_is_exact").cast(pl.Boolean, strict=True),
    )
    execution = execution_bars.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
        pl.col("open").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
    )
    members = universe_membership.select(
        pl.col("instrument").cast(pl.String),
        pl.col("date").cast(pl.String),
    )

    for name, frame in (
        ("adjusted_bars", prices),
        ("execution_bars", execution),
        ("universe_membership", members),
    ):
        if frame.select(
            pl.any_horizontal(
                pl.col("instrument").is_null(),
                pl.col("date").is_null(),
            ).any()
        ).item():
            raise ValueError(f"{name} contains null instrument/date keys")
        if frame.select(
            pl.struct(["instrument", "date"]).is_duplicated().any()
        ).item():
            raise ValueError(f"{name} contains duplicate instrument/date keys")
        outside = set(frame["date"].to_list()).difference(sessions)
        if outside:
            raise ValueError(
                f"{name} contains dates outside trading calendar: {sorted(outside)[:3]}"
            )

    if prices.filter(
        pl.col("adj_close").is_null()
        | (~pl.col("adj_close").is_finite())
        | (pl.col("adj_close") <= 0)
        | pl.col("adj_is_exact").is_null()
        | (pl.col("adj_is_exact") != True)
    ).height:
        raise ValueError("HFQ adjusted close must be finite, positive and exact")
    if execution.filter(
        pl.col("open").is_null()
        | pl.col("close").is_null()
        | (~pl.col("open").is_finite())
        | (~pl.col("close").is_finite())
        | (pl.col("open") <= 0)
        | (pl.col("close") <= 0)
    ).height:
        raise ValueError("Raw execution open/close must be finite and positive")

    # Use the exact per-session HFQ multiplier; never combine adjusted close
    # with an unadjusted open or infer the multiplier from another date.
    adjusted_opens = (
        execution.join(
            prices,
            on=["instrument", "date"],
            how="inner",
            validate="1:1",
        )
        .with_columns(
            (pl.col("open") * pl.col("adj_close") / pl.col("close")).alias("adj_open")
        )
        .select("instrument", "date", "adj_open")
    )
    if adjusted_opens.filter(
        pl.col("adj_open").is_null()
        | (~pl.col("adj_open").is_finite())
        | (pl.col("adj_open") <= 0)
    ).height:
        raise ValueError("Derived HFQ adjusted open must be finite and positive")

    weekly_dates = weekly_rebalance_dates(sessions)
    session_index = {session: i for i, session in enumerate(sessions)}
    rows: list[dict[str, Any]] = []

    # A signal's holding window ends at the first executable open after the
    # following weekly signal. This naturally handles shortened holiday weeks.
    for signal_position, signal_date in enumerate(weekly_dates[:-1]):
        next_signal_date = weekly_dates[signal_position + 1]
        entry_index = session_index[signal_date] + 1
        exit_index = session_index[next_signal_date] + 1
        if entry_index >= len(sessions) or exit_index >= len(sessions):
            continue
        entry_date = sessions[entry_index]
        exit_date = sessions[exit_index]
        signal_members = members.filter(pl.col("date") == signal_date).select("instrument")

        entry_prices = adjusted_opens.filter(pl.col("date") == entry_date).select(
            "instrument", pl.col("adj_open").alias("entry_price")
        )
        exit_prices = adjusted_opens.filter(pl.col("date") == exit_date).select(
            "instrument", pl.col("adj_open").alias("exit_price")
        )
        matched = (
            signal_members.join(entry_prices, on="instrument", how="inner", validate="1:1")
            .join(exit_prices, on="instrument", how="inner", validate="1:1")
            .with_columns(
                (pl.col("exit_price") / pl.col("entry_price") - 1.0).alias("forward_return")
            )
        )
        rows.extend(
            {
                "signal_date": signal_date,
                "instrument": row["instrument"],
                "label_entry_date": entry_date,
                "label_end_date": exit_date,
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
            "label_entry_date": pl.String,
            "label_end_date": pl.String,
            "forward_return": pl.Float64,
        },
    )
