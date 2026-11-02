"""Deterministic validation for canonical market panels."""

import polars as pl

BAR_COLUMNS = {"instrument", "date", "open", "high", "low", "close", "volume", "amount"}


def validate_daily_bars(frame: pl.DataFrame) -> None:
    """Raise ValueError when required columns or basic OHLC invariants fail."""
    missing = BAR_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing required daily-bar columns: {sorted(missing)}")
    if frame.select(pl.any_horizontal(pl.all().is_null()).any()).item():
        raise ValueError("Daily bars contain null values")
    invalid = (
        (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
        | (pl.col("volume") < 0)
        | (pl.col("amount") < 0)
    )
    if frame.select(invalid.any()).item():
        raise ValueError("Daily bars violate OHLC/volume/amount invariants")
    if frame.select(pl.struct(["instrument", "date"]).is_duplicated().any()).item():
        raise ValueError("Duplicate (instrument, date) keys")
