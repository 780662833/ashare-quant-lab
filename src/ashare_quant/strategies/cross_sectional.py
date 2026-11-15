"""Weekly model-score ranking into the frozen top-20 target-weight contract."""
from __future__ import annotations

import polars as pl

from ashare_quant.backtest.engine import equal_weight_targets


def weekly_cross_sectional_model_targets(
    predictions: pl.DataFrame,
    *,
    top_n: int = 20,
) -> pl.DataFrame:
    """Rank each weekly cross-section by predicted forward return, then equal-weight.

    The function emits target weights only. It does not execute orders or
    simulate cash, fills, fees, suspensions or T+1; the shared engine owns those.
    """
    required = {"signal_date", "instrument", "predicted_return"}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError(f"Model predictions missing columns: {sorted(missing)}")
    return equal_weight_targets(
        predictions.select(
            "signal_date",
            "instrument",
            pl.col("predicted_return").alias("score"),
        ),
        top_n=top_n,
    )
