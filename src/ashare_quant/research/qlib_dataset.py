"""Optional Qlib DatasetH bridge for admitted Phase 9 training panels.

Project-owned Polars frames remain the source of truth. Qlib receives only
admitted training rows plus explicitly separated, feature-only inference rows.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    _frame_fingerprint,
)

_KEYS = ["signal_date", "instrument"]


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


@dataclass(frozen=True, slots=True)
class QlibDatasetBundle:
    """Qlib DatasetH bound to the exact project admission that created it."""

    dataset: Any
    admission: TrainingDatasetAdmission
    feature_columns: tuple[str, ...]
    segment_bounds: Mapping[str, tuple[str, str]]
    training_rows: int | None = None

    def prepare(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate the Qlib DatasetH prepare interface."""
        return self.dataset.prepare(*args, **kwargs)

    @property
    def handler(self) -> Any:
        """Expose the underlying handler for diagnostics and compatibility."""
        return getattr(self.dataset, "handler", None)

    @property
    def segments(self) -> dict[str, tuple[str, str]]:
        """Return a defensive copy of the Qlib segment mapping."""
        return dict(self.segment_bounds)


def build_qlib_panel_rows(
    *,
    features: pl.DataFrame,
    labels: pl.DataFrame,
    admission: TrainingDatasetAdmission,
) -> tuple[list[tuple[str, str]], list[str], list[float]]:
    """Return sorted Qlib keys, feature names and mature training labels.

    Both complete frame fingerprints must match the successful admission. This
    prevents a caller from swapping or trimming training frames after admission.
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
    # Admission allows feature rows whose forward label is not available
    # (for example, missing future price endpoints). Every admitted label must
    # still match one admitted feature; only complete mature pairs enter Qlib.
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
    # Admission fingerprints and counts describe the complete source frames.
    # Supervised Qlib training uses only rows with a mature label and a complete
    # finite feature vector. Do not impute null features or silently mutate the
    # admitted source frames.
    complete_row = pl.all_horizontal(
        pl.col("forward_return").is_not_null() & pl.col("forward_return").is_finite(),
        *[
            pl.col(name).is_not_null() & pl.col(name).is_finite()
            for name in admission.feature_columns
        ],
    )
    ordered = joined.filter(complete_row).sort(_KEYS)
    if ordered.is_empty():
        raise ValueError("Qlib panel has no complete finite feature/label rows")
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
    inference_features: pl.DataFrame | None = None,
) -> QlibDatasetBundle:
    """Create an admission-bound DatasetH with feature-only inference segments.

    features/labels contain only the rows approved for model fitting. Optional
    inference_features contain validation or test signal rows and are kept
    label-free inside Qlib. The project admission never includes those rows.
    """
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
    if "train" not in normalized:
        raise ValueError("Qlib DatasetH requires an explicit train segment")
    chronological = sorted(normalized.items(), key=lambda item: item[1][0])
    for (_, previous), (_, current) in zip(chronological, chronological[1:]):
        if current[0] <= previous[1]:
            raise ValueError("Qlib dataset segments must not overlap")

    index, feature_names, _targets = build_qlib_panel_rows(
        features=features, labels=labels, admission=admission
    )
    train_start, train_end = normalized["train"]
    if any(not train_start <= signal_date <= train_end for signal_date, _ in index):
        raise ValueError("admitted training rows must fall inside the Qlib train segment")

    eligible_keys = pl.DataFrame(
        index,
        schema={"signal_date": pl.String, "instrument": pl.String},
        orient="row",
    )
    training_panel = (
        features.select(
            pl.col("signal_date").cast(pl.String),
            pl.col("instrument").cast(pl.String),
            *[pl.col(name).cast(pl.Float64) for name in feature_names],
        )
        .join(
            labels.select(
                pl.col("signal_date").cast(pl.String),
                pl.col("instrument").cast(pl.String),
                pl.col("forward_return").cast(pl.Float64),
            ),
            on=_KEYS,
            how="inner",
            validate="1:1",
        )
        .join(eligible_keys, on=_KEYS, how="inner", validate="1:1")
        .sort(_KEYS)
    )

    frames = [training_panel]
    if inference_features is not None:
        if not isinstance(inference_features, pl.DataFrame):
            raise TypeError("inference_features must be a Polars DataFrame")
        required_inference = set(_KEYS).union(feature_names)
        if not required_inference.issubset(inference_features.columns):
            raise ValueError("inference features do not match the admitted feature schema")
        inference_panel = inference_features.select(
            pl.col("signal_date").cast(pl.String),
            pl.col("instrument").cast(pl.String),
            *[pl.col(name).cast(pl.Float64) for name in feature_names],
        )
        if inference_panel.select(pl.struct(_KEYS).is_duplicated().any()).item():
            raise ValueError("inference features contain duplicate signal_date/instrument keys")
        if inference_panel.select(
            pl.any_horizontal(
                pl.col("signal_date").is_null(),
                pl.col("instrument").is_null(),
            ).any()
        ).item():
            raise ValueError("inference features contain null keys")
        for signal_date in inference_panel["signal_date"].to_list():
            _iso_date(signal_date, "inference_features.signal_date")
        inference_panel = inference_panel.drop_nulls(subset=feature_names)
        if inference_panel.filter(
            pl.any_horizontal(*[~pl.col(name).is_finite() for name in feature_names])
        ).height:
            raise ValueError("inference features contain non-finite values")
        for signal_date in inference_panel["signal_date"].unique().to_list():
            matching = [
                name
                for name, (start, end) in normalized.items()
                if name != "train" and start <= signal_date <= end
            ]
            if len(matching) != 1:
                raise ValueError(
                    "inference feature rows must fall inside exactly one non-train segment"
                )
        inference_panel = inference_panel.with_columns(
            pl.lit(float("nan"), dtype=pl.Float64).alias("forward_return")
        )
        frames.append(inference_panel)

    combined = pl.concat(frames, how="vertical").sort(_KEYS)
    if combined.select(pl.struct(_KEYS).is_duplicated().any()).item():
        raise ValueError("Qlib dataset contains duplicate signal_date/instrument keys")
    combined_index = list(
        zip(
            combined["signal_date"].to_list(),
            combined["instrument"].to_list(),
            strict=True,
        )
    )
    values = [
        [float(value) for value in row]
        for row in combined.select(*feature_names, "forward_return").rows()
    ]

    try:
        import pandas as pd
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP
    except ImportError as exc:
        raise RuntimeError(
            "Qlib dataset support requires the optional 'research' dependencies"
        ) from exc

    multi_index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime([signal_date for signal_date, _ in combined_index]),
            [instrument for _, instrument in combined_index],
        ],
        names=["datetime", "instrument"],
    )
    column_keys = (
        [("feature", name) for name in feature_names]
        + [("label", "forward_return")]
    )
    columns = pd.MultiIndex.from_tuples(column_keys)
    frame = pd.DataFrame(values, index=multi_index, columns=columns)
    handler = DataHandlerLP.from_df(frame)
    dataset = DatasetH(handler=handler, segments=normalized)
    return QlibDatasetBundle(
        dataset=dataset,
        admission=admission,
        feature_columns=tuple(feature_names),
        segment_bounds=dict(normalized),
        training_rows=len(index),
    )
