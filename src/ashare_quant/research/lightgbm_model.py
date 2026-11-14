"""Deterministic LightGBM regression adapter behind Phase 9 admission."""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import polars as pl

from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    _frame_fingerprint,
)

_MODEL_SCHEMA_VERSION = 1
_KEYS = ["signal_date", "instrument"]


@dataclass(frozen=True, slots=True)
class LightGBMConfig:
    """Small, deterministic first-model configuration; no parameter search."""

    num_boost_round: int = 200
    min_training_rows: int = 1000
    learning_rate: float = 0.05
    num_leaves: int = 31
    max_depth: int = -1
    min_data_in_leaf: int = 50
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l1: float = 0.0
    lambda_l2: float = 1.0
    seed: int = 42
    num_threads: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        for name, value in (
            ("num_boost_round", self.num_boost_round),
            ("min_training_rows", self.min_training_rows),
            ("num_leaves", self.num_leaves),
            ("min_data_in_leaf", self.min_data_in_leaf),
            ("bagging_freq", self.bagging_freq),
            ("num_threads", self.num_threads),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or self.max_depth == 0 or self.max_depth < -1:
            raise ValueError("max_depth must be -1 or a positive integer")
        for name, value in (
            ("learning_rate", self.learning_rate),
            ("feature_fraction", self.feature_fraction),
            ("bagging_fraction", self.bagging_fraction),
            ("lambda_l1", self.lambda_l1),
            ("lambda_l2", self.lambda_l2),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        if not 0 < self.learning_rate <= 1:
            raise ValueError("learning_rate must be in (0, 1]")
        if not 0 < self.feature_fraction <= 1 or not 0 < self.bagging_fraction <= 1:
            raise ValueError("feature_fraction and bagging_fraction must be in (0, 1]")


@dataclass(slots=True)
class LightGBMRegressionModel:
    """Trained Booster plus the exact feature/provenance contract."""

    booster: Any
    feature_columns: tuple[str, ...]
    config: LightGBMConfig
    acceptance_report_id: str
    training_cutoff: str
    training_rows: int
    source_fingerprint: str
    feature_fingerprint: str
    label_fingerprint: str

    def predict(self, features: pl.DataFrame) -> pl.DataFrame:
        """Predict only rows with complete finite feature values."""
        required = set(_KEYS).union(self.feature_columns)
        missing = required.difference(features.columns)
        if missing:
            raise ValueError(f"Prediction features missing columns: {sorted(missing)}")
        frame = features.select(
            pl.col("signal_date").cast(pl.String),
            pl.col("instrument").cast(pl.String),
            *[pl.col(name).cast(pl.Float64).alias(name) for name in self.feature_columns],
        )
        if frame.select(pl.struct(_KEYS).is_duplicated().any()).item():
            raise ValueError("Prediction features contain duplicate signal_date/instrument keys")
        if frame.select(
            pl.any_horizontal(pl.col("signal_date").is_null(), pl.col("instrument").is_null()).any()
        ).item():
            raise ValueError("Prediction features contain null keys")
        frame = frame.drop_nulls(subset=list(self.feature_columns))
        if frame.is_empty():
            return pl.DataFrame(
                schema={
                    "signal_date": pl.String,
                    "instrument": pl.String,
                    "predicted_return": pl.Float64,
                }
            )
        if frame.filter(
            pl.any_horizontal(
                *[~pl.col(name).is_finite() for name in self.feature_columns]
            )
        ).height:
            raise ValueError("Prediction features contain non-finite values")
        matrix = frame.select(*self.feature_columns).to_numpy()
        values = self.booster.predict(matrix)
        predictions = [float(value) for value in values]
        if len(predictions) != frame.height or any(
            not value == value or value in (float("inf"), float("-inf"))
            for value in predictions
        ):
            raise ValueError("LightGBM returned invalid prediction values")
        return frame.select(*_KEYS).with_columns(
            pl.Series("predicted_return", predictions, dtype=pl.Float64)
        )

    def save(self, path: str | Path) -> Path:
        """Save Booster and a checksum-bound JSON contract sidecar."""
        model_path = Path(path).expanduser()
        metadata_path = model_path.with_suffix(model_path.suffix + ".json")
        model_path.parent.mkdir(parents=True, exist_ok=True)
        if model_path.exists() or metadata_path.exists():
            raise FileExistsError("Refusing to overwrite an existing model artifact")
        temporary_model = model_path.with_name(model_path.name + ".tmp")
        temporary_metadata = metadata_path.with_name(metadata_path.name + ".tmp")
        try:
            self.booster.save_model(str(temporary_model))
            digest = hashlib.sha256(temporary_model.read_bytes()).hexdigest()
            metadata = {
                "schema_version": _MODEL_SCHEMA_VERSION,
                "feature_columns": list(self.feature_columns),
                "config": asdict(self.config),
                "acceptance_report_id": self.acceptance_report_id,
                "training_cutoff": self.training_cutoff,
                "training_rows": self.training_rows,
                "source_fingerprint": self.source_fingerprint,
                "feature_fingerprint": self.feature_fingerprint,
                "label_fingerprint": self.label_fingerprint,
                "model_sha256": digest,
                "admission_accepted": True,
                "training_performed": True,
            }
            temporary_metadata.write_text(
                json.dumps(metadata, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_model, model_path)
            os.replace(temporary_metadata, metadata_path)
        except Exception:
            temporary_model.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)
            model_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise
        return model_path

    @classmethod
    def load(cls, path: str | Path) -> LightGBMRegressionModel:
        """Load only when the model bytes match the saved metadata checksum."""
        model_path = Path(path).expanduser()
        metadata_path = model_path.with_suffix(model_path.suffix + ".json")
        if not model_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError("Model artifact or metadata sidecar is missing")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("schema_version") != _MODEL_SCHEMA_VERSION:
            raise ValueError("Unsupported LightGBM model metadata schema")
        if metadata.get("admission_accepted") is not True or metadata.get("training_performed") is not True:
            raise ValueError("Model metadata does not confirm admitted training")
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if digest != metadata.get("model_sha256"):
            raise ValueError("LightGBM model checksum does not match metadata")
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(model_path))
        return cls(
            booster=booster,
            feature_columns=tuple(metadata["feature_columns"]),
            config=LightGBMConfig(**metadata["config"]),
            acceptance_report_id=metadata["acceptance_report_id"],
            training_cutoff=metadata["training_cutoff"],
            training_rows=int(metadata["training_rows"]),
            source_fingerprint=metadata["source_fingerprint"],
            feature_fingerprint=metadata["feature_fingerprint"],
            label_fingerprint=metadata["label_fingerprint"],
        )


def fit_lightgbm_regressor(
    *,
    features: pl.DataFrame,
    labels: pl.DataFrame,
    admission: TrainingDatasetAdmission,
    config: LightGBMConfig | None = None,
) -> LightGBMRegressionModel:
    """Fit only after a matching, successful Phase 9 admission decision.

    This function is deliberately never invoked by CI or repository setup.
    """
    if not isinstance(admission, TrainingDatasetAdmission) or admission.admitted is not True:
        raise PermissionError("A successful TrainingDatasetAdmission is required before fitting")
    if _frame_fingerprint(features, keys=_KEYS) != admission.feature_fingerprint:
        raise ValueError("Feature frame differs from the admitted feature fingerprint")
    if _frame_fingerprint(labels, keys=_KEYS) != admission.label_fingerprint:
        raise ValueError("Label frame differs from the admitted label fingerprint")
    cfg = config or LightGBMConfig()
    feature_columns = tuple(admission.feature_columns)
    if not feature_columns:
        raise ValueError("Admission contains no model feature columns")
    required_labels = {"signal_date", "instrument", "label_end_date", "forward_return"}
    if not required_labels.issubset(labels.columns):
        raise ValueError("Labels do not satisfy the forward-return contract")

    joined = labels.join(features, on=_KEYS, how="inner")
    joined = joined.filter(
        (pl.col("label_end_date").cast(pl.String) < admission.training_cutoff)
        & (pl.col("signal_date").cast(pl.String) < admission.training_cutoff)
    )
    joined = joined.drop_nulls(subset=[*feature_columns, "forward_return"])
    if joined.is_empty():
        raise ValueError("No complete, mature feature/label rows are available for training")
    if joined.filter(
        pl.any_horizontal(
            *[
                ~pl.col(name).cast(pl.Float64).is_finite()
                for name in [*feature_columns, "forward_return"]
            ]
        )
    ).height:
        raise ValueError("Training matrix contains non-finite feature or label values")
    if joined.height < cfg.min_training_rows:
        raise ValueError(
            "Insufficient complete training rows: "
            f"observed={joined.height}, required={cfg.min_training_rows}"
        )
    import lightgbm as lgb

    matrix = joined.select(*feature_columns).to_numpy()
    target = joined["forward_return"].to_numpy()
    dataset = lgb.Dataset(matrix, label=target, feature_name=list(feature_columns))
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
    booster = lgb.train(params, dataset, num_boost_round=cfg.num_boost_round)
    return LightGBMRegressionModel(
        booster=booster,
        feature_columns=feature_columns,
        config=cfg,
        acceptance_report_id=admission.report_id,
        training_cutoff=admission.training_cutoff,
        training_rows=joined.height,
        source_fingerprint=admission.source_fingerprint,
        feature_fingerprint=admission.feature_fingerprint,
        label_fingerprint=admission.label_fingerprint,
    )
