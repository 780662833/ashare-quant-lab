from __future__ import annotations

from dataclasses import replace

import lightgbm as lgb
import numpy as np
import pandas as pd
import polars as pl
from qlib.data.dataset.handler import DataHandlerLP

from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    _frame_fingerprint,
)
from ashare_quant.research.lightgbm_model import (
    LightGBMConfig,
    LightGBMRegressionModel,
)
from ashare_quant.research.qlib_dataset import create_qlib_dataset
from ashare_quant.research.qlib_lightgbm import predict_qlib_lightgbm_regressor


class SyntheticBooster:
    """Prediction stub; this smoke test must never fit a real model."""

    def predict(self, matrix):
        return np.linspace(0.01, 0.02, num=len(matrix), dtype=np.float64)


def _admitted_synthetic_frames():
    features = pl.DataFrame(
        {
            "signal_date": [
                "2024-01-05",
                "2024-01-05",
                "2024-01-12",
            ],
            "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "return_5": [0.02, -0.01, 0.03],
            "pit_roe": [0.10, 0.12, 0.08],
        }
    )
    labels = pl.DataFrame(
        {
            "signal_date": ["2024-01-05", "2024-01-05"],
            "instrument": ["000001.SZ", "000002.SZ"],
            "label_end_date": ["2024-01-12", "2024-01-12"],
            "forward_return": [0.03, -0.02],
        }
    )
    admission = TrainingDatasetAdmission(
        admitted=True,
        report_id="qlib-real-api-synthetic-smoke",
        start_date="2024-01-01",
        end_date="2024-01-31",
        universe_profile="synthetic-qlib-smoke",
        training_cutoff="2024-01-19",
        feature_rows=features.height,
        label_rows=labels.height,
        feature_columns=("return_5", "pit_roe"),
        label_horizon_sessions=5,
        source_fingerprint="a" * 64,
        feature_fingerprint=_frame_fingerprint(
            features, keys=["signal_date", "instrument"]
        ),
        label_fingerprint=_frame_fingerprint(
            labels, keys=["signal_date", "instrument"]
        ),
        rejection_reasons=(),
    )
    inference_features = pl.DataFrame(
        {
            "signal_date": ["2024-01-19", "2024-01-19"],
            "instrument": ["000004.SZ", "600000.SH"],
            "return_5": [0.04, 0.01],
            "pit_roe": [0.15, 0.09],
        }
    )
    return features, labels, admission, inference_features


def test_real_qlib_dataseth_prepare_and_lightgbm_dataset_contract():
    """Exercise actual Qlib 0.9.7 APIs with synthetic rows only; never call lgb.train."""
    features, labels, admission, inference_features = _admitted_synthetic_frames()
    dataset_bundle = create_qlib_dataset(
        features=features,
        labels=labels,
        admission=admission,
        segments={
            "train": ("2024-01-01", "2024-01-12"),
            "valid": ("2024-01-19", "2024-01-19"),
        },
        inference_features=inference_features,
    )

    assert isinstance(dataset_bundle.handler, DataHandlerLP)
    train = dataset_bundle.prepare(
        "train",
        col_set=["feature", "label"],
        data_key=DataHandlerLP.DK_L,
    )
    assert isinstance(train, pd.DataFrame)
    assert train.index.names == ["datetime", "instrument"]
    assert isinstance(train.index.get_level_values("datetime"), pd.DatetimeIndex)
    assert len(train) == 2
    assert train["feature"].columns.tolist() == ["return_5", "pit_roe"]
    assert train["label"].columns.tolist() == ["forward_return"]
    assert train.index.get_level_values("instrument").tolist() == [
        "000001.SZ",
        "000002.SZ",
    ]

    train_features = train["feature"].to_numpy(dtype=np.float64)
    train_labels = train["label"]["forward_return"].to_numpy(dtype=np.float64)
    assert np.isfinite(train_features).all()
    assert np.isfinite(train_labels).all()

    # Exercise the actual LightGBM Dataset constructor only. Do not fit/train.
    lightgbm_dataset = lgb.Dataset(
        train_features,
        label=train_labels,
        feature_name=list(admission.feature_columns),
    )
    lightgbm_dataset.construct()
    assert lightgbm_dataset.num_data() == 2
    assert lightgbm_dataset.num_feature() == 2

    valid = dataset_bundle.prepare(
        "valid",
        col_set="feature",
        data_key=DataHandlerLP.DK_I,
    )
    assert isinstance(valid, pd.DataFrame)
    assert valid.index.names == ["datetime", "instrument"]
    assert isinstance(valid.index.get_level_values("datetime"), pd.DatetimeIndex)
    assert valid.columns.tolist() == ["return_5", "pit_roe"]
    assert valid.index.get_level_values("instrument").tolist() == [
        "000004.SZ",
        "600000.SH",
    ]

    # Exercise the project's real Qlib inference adapter with a prediction-only
    # stub. The test intentionally does not invoke LightGBM's training API.
    model = LightGBMRegressionModel(
        booster=SyntheticBooster(),
        feature_columns=admission.feature_columns,
        config=LightGBMConfig(min_training_rows=2),
        acceptance_report_id=admission.report_id,
        training_cutoff=admission.training_cutoff,
        training_rows=admission.label_rows,
        source_fingerprint=admission.source_fingerprint,
        feature_fingerprint=admission.feature_fingerprint,
        label_fingerprint=admission.label_fingerprint,
    )
    predictions = predict_qlib_lightgbm_regressor(
        model=model,
        dataset=dataset_bundle,
        segment="valid",
    )
    assert predictions.select("signal_date", "instrument").to_dicts() == [
        {"signal_date": "2024-01-19", "instrument": "000004.SZ"},
        {"signal_date": "2024-01-19", "instrument": "600000.SH"},
    ]
    assert predictions["predicted_return"].to_list() == [0.01, 0.02]
