"""Shared row-eligibility and transport rules for model features.

Market-derived features must be present and finite. A strict PIT fundamental
feature may be null when no report was knowable by the signal timestamp; that
null is transported to LightGBM as NaN (its native missing-value marker), not
imputed or forward-filled.
"""
from __future__ import annotations

from collections.abc import Sequence

import polars as pl

PIT_FEATURE_PREFIX = "pit_"


def feature_eligibility_expression(feature_columns: Sequence[str]) -> pl.Expr:
    """Return a row predicate allowing null only in strict PIT feature columns.

    A non-null NaN or infinity is invalid even for PIT columns. The PIT builder
    represents unavailable facts as null; that distinction prevents upstream
    numeric corruption from being mistaken for an unavailable report.
    """
    columns = tuple(feature_columns)
    if not columns:
        raise ValueError("feature_columns must not be empty")
    checks: list[pl.Expr] = []
    for name in columns:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("feature column names must be non-empty strings")
        value = pl.col(name)
        if name.startswith(PIT_FEATURE_PREFIX):
            checks.append(value.is_null() | value.is_finite())
        else:
            checks.append(value.is_not_null() & value.is_finite())
    return pl.all_horizontal(*checks)


def pit_nulls_as_nan(
    frame: pl.DataFrame,
    feature_columns: Sequence[str],
) -> pl.DataFrame:
    """Encode true null PIT facts as NaN for the LightGBM numeric interface."""
    pit_columns = [
        name for name in feature_columns if name.startswith(PIT_FEATURE_PREFIX)
    ]
    if not pit_columns:
        return frame
    return frame.with_columns(
        *[
            pl.col(name).cast(pl.Float64).fill_null(float("nan")).alias(name)
            for name in pit_columns
        ]
    )
