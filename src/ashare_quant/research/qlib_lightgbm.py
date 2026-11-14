"""LightGBM fit/predict adapters that consume the admitted Qlib DatasetH path.

This module never assembles features, decides PIT availability, or authorizes
training. It only reads project-admitted Qlib segments and delegates execution
back to the project's existing model/strategy contracts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from ashare_quant.research.admission import TrainingDatasetAdmission
from ashare_quant.research.lightgbm_model import (
    LightGBMConfig,
    LightGBMRegressionModel,
)
from ashare_quant.research.qlib_dataset import QlibDatasetBundle


def _require_bound_dataset(
    dataset: QlibDatasetBundle,
    admission: TrainingDatasetAdmission,
) -> None:
    if not isinstance(dataset, QlibDatasetBundle):
        raise TypeError("Qlib training requires a QlibDatasetBundle created by this project")
    if not isinstance(admission, TrainingDatasetAdmission) or not admission.admitted:
        raise PermissionError("A successful TrainingDatasetAdmission is required")
    if dataset.admission != admission:
        raise ValueError("Qlib dataset admission does not match the supplied admission")
    if dataset.feature_columns != tuple(admission.feature_columns):
        raise ValueError("Qlib dataset feature schema differs from admission")


def _iso_index_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if len(text) > 10:
        text = text[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Qlib index contains a non-date datetime value") from exc
    if parsed.isoformat() != text:
        raise ValueError("Qlib index dates must use canonical YYYY-MM-DD values")
    return text


def _index_keys(frame: Any) -> list[tuple[str, str]]:
    index = getattr(frame, "index", None)
    if index is None or getattr(index, "nlevels", None) != 2:
        raise ValueError("Qlib data must use a two-level datetime/instrument index")
    names = getattr(index, "names", None)
    if names is not None and list(names) != ["datetime", "instrument"]:
        raise ValueError("Qlib index levels must be named datetime and instrument")
    if getattr(index, "is_unique", True) is not True:
        raise ValueError("Qlib data contains duplicate datetime/instrument keys")
    if not hasattr(index, "get_level_values"):
        raise ValueError("Qlib index does not expose datetime/instrument levels")
    date_values = index.get_level_values(0)
    instrument_values = index.get_level_values(1)
    dates = date_values.tolist() if hasattr(date_values, "tolist") else list(date_values)
    instruments = (
        instrument_values.tolist()
        if hasattr(instrument_values, "tolist")
        else list(instrument_values)
    )
    keys = [(_iso_index_value(day), str(symbol)) for day, symbol in zip(dates, instruments, strict=True)]
    if any(not symbol for _, symbol in keys):
        raise ValueError("Qlib index contains an empty instrument key")
    if len(set(keys)) != len(keys):
        raise ValueError("Qlib data contains duplicate datetime/instrument keys")
    return keys


def _numpy_values(frame: Any, *, field: str):
    import numpy as np

    values = frame.to_numpy() if hasattr(frame, "to_numpy") else frame.values
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Qlib {field} data must be numeric") from exc
    return array


def _training_arrays(prepared: Any, feature_columns: tuple[str, ...]):
    import numpy as np

    try:
        feature_frame = prepared["feature"]
        label_frame = prepared["label"]
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("Qlib training segment must expose feature and label column groups") from exc

    observed_features = tuple(str(value) for value in feature_frame.columns)
    observed_labels = tuple(str(value) for value in label_frame.columns)
    if observed_features != feature_columns:
        raise ValueError("Qlib training feature columns differ from the admitted schema")
    if observed_labels != ("forward_return",):
        raise ValueError("Qlib training must expose exactly one forward_return label")

    keys = _index_keys(prepared)
    matrix = _numpy_values(feature_frame, field="feature")
    target = _numpy_values(label_frame, field="label")
    if matrix.ndim != 2 or matrix.shape[1] != len(feature_columns):
        raise ValueError("Qlib feature matrix has an invalid shape")
    if target.ndim == 2 and target.shape[1] == 1:
        target = np.squeeze(target, axis=1)
    elif target.ndim != 1:
        raise ValueError("LightGBM requires exactly one one-dimensional label")
    if matrix.shape[0] != len(keys) or target.shape[0] != len(keys):
        raise ValueError("Qlib features, labels and index rows are not aligned")
    if matrix.shape[0] == 0:
        raise ValueError("Qlib train segment is empty")
    if not np.isfinite(matrix).all() or not np.isfinite(target).all():
        raise ValueError("Qlib training matrix contains null or non-finite values")
    return matrix, target, keys


def fit_qlib_lightgbm_regressor(
    *,
    dataset: QlibDatasetBundle,
    admission: TrainingDatasetAdmission,
    train_segment: str = "train",
    config: LightGBMConfig | None = None,
) -> LightGBMRegressionModel:
    """Fit the existing deterministic LightGBM contract from Qlib's learn view."""
    _require_bound_dataset(dataset, admission)
    if not isinstance(train_segment, str) or train_segment not in dataset.segment_bounds:
        raise ValueError("train_segment is not present in the Qlib dataset")
    _, segment_end = dataset.segment_bounds[train_segment]
    if segment_end >= admission.training_cutoff:
        raise ValueError("Qlib train segment must end strictly before the admission cutoff")

    try:
        from qlib.data.dataset.handler import DataHandlerLP
    except ImportError as exc:
        raise RuntimeError(
            "Qlib LightGBM integration requires the optional 'research' dependencies"
        ) from exc

    prepared = dataset.prepare(
        train_segment,
        col_set=["feature", "label"],
        data_key=DataHandlerLP.DK_L,
    )
    matrix, target, keys = _training_arrays(prepared, dataset.feature_columns)
    if any(signal_date >= admission.training_cutoff for signal_date, _ in keys):
        raise ValueError("Qlib train segment contains signal rows at or beyond the cutoff")
    if len(keys) != admission.label_rows:
        raise ValueError("Qlib train segment row count differs from the admitted dataset")

    cfg = config or LightGBMConfig()
    if len(keys) < cfg.min_training_rows:
        raise ValueError(
            "Insufficient complete training rows: "
            f"observed={len(keys)}, required={cfg.min_training_rows}"
        )

    import lightgbm as lgb

    train_data = lgb.Dataset(
        matrix,
        label=target,
        feature_name=list(dataset.feature_columns),
    )
    params = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": cfg.learning_rate,
        "num_leaves": cfg.num_leaves,
        "max_depth": cfg.max_depth,
        "min_data_in_leaf": cfg.min_data_in_leaf,
        "feature_fraction": cfg.feature_fraction,
        "bagging_fraction": cfg.bagging_fraction,
        "bagging_freq": cfg.bagging_freq,
        "lambda_l1": cfg.lambda_l1,
        "lambda_l2": cfg.lambda_l2,
        "seed": cfg.seed,
        "feature_fraction_seed": cfg.seed,
        "bagging_seed": cfg.seed,
        "data_random_seed": cfg.seed,
        "num_threads": cfg.num_threads,
        "deterministic": True,
        "force_col_wise": True,
        "verbosity": -1,
    }
    booster = lgb.train(params, train_data, num_boost_round=cfg.num_boost_round)
    return LightGBMRegressionModel(
        booster=booster,
        feature_columns=dataset.feature_columns,
        config=cfg,
        acceptance_report_id=admission.report_id,
        training_cutoff=admission.training_cutoff,
        training_rows=len(keys),
        source_fingerprint=admission.source_fingerprint,
        feature_fingerprint=admission.feature_fingerprint,
        label_fingerprint=admission.label_fingerprint,
    )


def predict_qlib_lightgbm_regressor(
    *,
    model: LightGBMRegressionModel,
    dataset: QlibDatasetBundle,
    segment: str,
) -> pl.DataFrame:
    """Predict one Qlib inference segment and return project-native keyed scores."""
    if not isinstance(model, LightGBMRegressionModel):
        raise TypeError("model must be a LightGBMRegressionModel")
    if not isinstance(dataset, QlibDatasetBundle):
        raise TypeError("prediction requires a project QlibDatasetBundle")
    admission = dataset.admission
    if (
        admission.report_id != model.acceptance_report_id
        or admission.training_cutoff != model.training_cutoff
        or admission.source_fingerprint != model.source_fingerprint
        or admission.feature_fingerprint != model.feature_fingerprint
        or admission.label_fingerprint != model.label_fingerprint
        or dataset.feature_columns != model.feature_columns
    ):
        raise ValueError("Qlib inference dataset does not match the trained model contract")
    if not isinstance(segment, str) or segment not in dataset.segment_bounds:
        raise ValueError("prediction segment is not present in the Qlib dataset")
    if segment == "train":
        raise ValueError("model predictions must use a non-train Qlib segment")
    segment_start, _ = dataset.segment_bounds[segment]
    if segment_start < model.training_cutoff:
        raise ValueError("Qlib inference segment starts before the model training cutoff")

    try:
        from qlib.data.dataset.handler import DataHandlerLP
    except ImportError as exc:
        raise RuntimeError(
            "Qlib LightGBM integration requires the optional 'research' dependencies"
        ) from exc

    prepared = dataset.prepare(
        segment,
        col_set="feature",
        data_key=DataHandlerLP.DK_I,
    )
    if getattr(prepared, "empty", False):
        return pl.DataFrame(
            schema={
                "signal_date": pl.String,
                "instrument": pl.String,
                "predicted_return": pl.Float64,
            }
        )

    observed_features = tuple(str(value) for value in prepared.columns)
    if observed_features != model.feature_columns:
        raise ValueError("Qlib inference feature columns differ from the trained model schema")
    keys = _index_keys(prepared)
    matrix = _numpy_values(prepared, field="inference feature")
    if matrix.ndim != 2 or matrix.shape != (len(keys), len(model.feature_columns)):
        raise ValueError("Qlib inference matrix has an invalid shape")
    if not matrix.size:
        return pl.DataFrame(
            schema={
                "signal_date": pl.String,
                "instrument": pl.String,
                "predicted_return": pl.Float64,
            }
        )
    import numpy as np

    if not np.isfinite(matrix).all():
        raise ValueError("Qlib inference matrix contains null or non-finite values")
    if any(signal_date < model.training_cutoff for signal_date, _ in keys):
        raise ValueError("Qlib inference segment contains a row before the model cutoff")

    values = [float(value) for value in model.booster.predict(matrix)]
    if len(values) != len(keys) or not np.isfinite(np.asarray(values, dtype=np.float64)).all():
        raise ValueError("LightGBM returned invalid Qlib inference predictions")
    return pl.DataFrame(
        {
            "signal_date": [day for day, _ in keys],
            "instrument": [symbol for _, symbol in keys],
            "predicted_return": values,
        }
    )
