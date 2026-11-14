"""Optional Qlib DatasetH bridge for admitted Phase 9 training panels.

CNEquity assembly, PIT checks, label maturity and admission remain project-owned.
This adapter only translates an already-admitted Polars panel into Qlib's
(datetime, instrument) MultiIndex / (feature, label) column contract.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import polars as pl

from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    _frame_fingerprint,
)

_KEYS = ["signal_date", "instrument"]
_RESERVED = {"signal_date", "instrument", "label_end_date", "forward_return"}


def _iso_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO YYYY-MM-DD string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use canonical YYYY-MM-DD")
    return value


def build_qlib_panel_rows(
    *,
    features: pl.DataFrame,
    labels: pl.DataFrame,
    admission: TrainingDatasetAdmission,
) -> tuple[list[tuple[str, str]], list[str], list[float]]:
    """Return sorted Qlib index keys, feature names and mature labels.

    The full input fingerprints must match the admission decision. This prevents
    a caller from swapping frames between admission and Qlib conversion.
    """
    if not isinstance(admission, TrainingDatasetAdmission) or not admission.admitted:
        raise ValueError("Qlib conversion requires an admitted training dataset")
    if not isinstance(features, pl.DataFrame) or not isinstance(labels, pl.DataFrame):
        raise TypeError("features and labels must be Polars DataFrames")
    required_features = set(_KEYS).union(admission.feature_columns)
    required_labels = set(_KEYS).union({"label_end_date", "forward_return"})
    if not required_features.issubset(features.columns):
        raise ValueError("features do not match the admitted feature schema")
    if not required_labels.issubset(labels.columns):
        raise ValueError("labels do not match the admitted label schema")
    if _frame_fingerprint(features, keys=_KEYS) != admission.feature_fingerprint:
        raise ValueError("feature frame fingerprint differs from admission")
    if _frame_fingerprint(labels, keys=_KEYS) != admission.label_fingerprint:
        raise ValueError("label frame fingerprint differs from admission")

    feature_frame = features.select(
        pl.col("signal_date").cast(pl.String),
        pl.col("instrument").cast(pl.String),
        *[pl.col(name).cast(pl.Float64) for name in admission.feature_columns],
    )
    label_frame = labels.select(
        pl.col("signal_date").cast(pl.String),
        pl.col("instrument").cast(pl.String),
        pl.col("label_end_date").cast(pl.String),
        pl.col("forward_return").cast(pl.Float64),
    )
    joined = feature_frame.join(label_frame, on=_KEYS, how="inner", validate="1:1")
    if joined.height != admission.label_rows:
        raise ValueError("Qlib training panel must contain every admitted label row")
    if joined.is_empty():
        raise ValueError("Qlib training panel is empty")
    if joined.filter(
        pl.col("label_end_date").is_null()
        | (pl.col("label_end_date") <= pl.col("signal_date"))
        | (pl.col("label_end_date") >= admission.training_cutoff)
    ).height:
        raise ValueError("Qlib panel contains labels that are not mature before the cutoff")
    if joined.filter(
        pl.any_horizontal(
            pl.col("forward_return").is_null(),
            ~pl.col("forward_return").is_finite(),
            *[
                pl.col(name).is_null() | ~pl.col(name).is_finite()
                for name in admission.feature_columns
            ],
        )
    ).height:
        raise ValueError("Qlib panel contains null or non-finite training values")

    ordered = joined.sort(_KEYS)
    dates = ordered["signal_date"].to_list()
    instruments = ordered["instrument"].to_list()
    for value in dates:
        _iso_date(value, "signal_date")
    index = list(zip(dates, instruments, strict=True))
    targets = ordered["forward_return"].to_list()
    return index, list(admission.feature_columns), [float(value) for value in targets]


def create_qlib_dataset(
    *,
    features: pl.DataFrame,
    labels: pl.DataFrame,
    admission: TrainingDatasetAdmission,
    segments: Mapping[str, tuple[str, str]],
) -> Any:
    """Create Qlib DatasetH lazily; Qlib/pandas are never base dependencies."""
    if not isinstance(segments, Mapping) or not segments:
        raise ValueError("segments must be a non-empty mapping")
    normalized: dict[str, tuple[str, str]] = {}
    for name, bounds in segments.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("segment names must be non-empty strings")
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError(f"segment {name!r} must contain start and end dates")
        start = _iso_date(bounds[0], f"{name}.start")
        end = _iso_date(bounds[1], f"{name}.end")
        if start > end:
            raise ValueError(f"segment {name!r} starts after it ends")
        normalized[name] = (start, end)
    chronological = sorted(normalized.items(), key=lambda item: item[1][0])
    for (_, previous), (_, current) in zip(chronological, chronological[1:]):
        if current[0] <= previous[1]:
            raise ValueError("Qlib dataset segments must not overlap")

    index, feature_names, targets = build_qlib_panel_rows(
        features=features, labels=labels, admission=admission
    )
    try:
        import pandas as pd
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP
    except ImportError as exc:
        raise RuntimeError(
            "Qlib dataset support requires the optional 'research' dependencies"
        ) from exc

    multi_index = pd.MultiIndex.from_tuples(
        index, names=["datetime", "instrument"]
    )
    column_keys = (
        [("feature", name) for name in feature_names]
        + [("label", "forward_return")]
    )
    columns = pd.MultiIndex.from_tuples(column_keys)
    panel = features.select(*_KEYS, *feature_names).join(
        labels.select(*_KEYS, "forward_return"),
        on=_KEYS,
        how="inner",
        validate="1:1",
    ).sort(_KEYS)
    values = [
        [float(row[name]) for name in feature_names] + [float(row["forward_return"])]
        for row in panel.to_dicts()
    ]
    frame = pd.DataFrame(values, index=multi_index, columns=columns)
    handler = DataHandlerLP.from_df(frame)
    return DatasetH(handler=handler, segments=normalized)
