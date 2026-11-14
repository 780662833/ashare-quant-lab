from __future__ import annotations

import polars as pl
import pytest

from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    _frame_fingerprint,
)
from ashare_quant.research.lightgbm_model import LightGBMConfig
from ashare_quant.research.qlib_dataset import create_qlib_dataset
from ashare_quant.research.qlib_lightgbm import (
    fit_qlib_lightgbm_regressor,
    predict_qlib_lightgbm_regressor,
)


class SyntheticBooster:
    """Prediction stub; this smoke test must never fit a real model."""

    def predict(self, matrix):
        return [0.01 + 0.01 * index for index in range(len(matrix))]


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
            "label_entry_date": ["2024-01-08", "2024-01-08"],
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


def test_real_qlib_dataseth_prepare_and_lightgbm_dataset_contract(monkeypatch):
    """Exercise actual Qlib 0.9.7 APIs with synthetic rows only; never fit a model."""
    lgb = pytest.importorskip("lightgbm", reason="install the optional research stack")
    pd = pytest.importorskip("pandas", reason="Qlib requires pandas")
    np = pytest.importorskip("numpy", reason="LightGBM requires NumPy")
    qlib_handler = pytest.importorskip(
        "qlib.data.dataset.handler",
        reason="install the optional Qlib 0.9.7 dependency",
    )
    DataHandlerLP = qlib_handler.DataHandlerLP
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
    assert dataset_bundle.training_rows == 2
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

    # Keep the real Qlib and LightGBM Dataset APIs, but replace only the final
    # LightGBM optimizer call so this smoke test cannot fit a model.
    captured = {}

    def no_fit(params, train_data, num_boost_round):
        train_data.construct()
        captured["params"] = params
        captured["dataset"] = train_data
        captured["num_boost_round"] = num_boost_round
        return SyntheticBooster()

    monkeypatch.setattr(lgb, "train", no_fit)
    model = fit_qlib_lightgbm_regressor(
        dataset=dataset_bundle,
        admission=admission,
        config=LightGBMConfig(min_training_rows=2, num_boost_round=3),
    )
    assert model.training_rows == 2
    assert captured["dataset"].num_data() == 2
    assert captured["dataset"].num_feature() == 2
    assert captured["dataset"].get_feature_name() == ["return_5", "pit_roe"]
    assert captured["num_boost_round"] == 3

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

    # The returned model carries a synthetic prediction-only Booster.
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
