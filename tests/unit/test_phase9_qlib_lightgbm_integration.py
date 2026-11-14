from __future__ import annotations

import sys
import types
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from ashare_quant.research.admission import TrainingDatasetAdmission
from ashare_quant.research.lightgbm_model import LightGBMConfig
from ashare_quant.research.qlib_dataset import QlibDatasetBundle
from ashare_quant.research.qlib_lightgbm import (
    fit_qlib_lightgbm_regressor,
    predict_qlib_lightgbm_regressor,
)


class FakeIndex:
    nlevels = 2
    names = ["datetime", "instrument"]
    is_unique = True

    def __init__(self, dates, instruments):
        self._dates = dates
        self._instruments = instruments

    def get_level_values(self, level):
        if level in (0, "datetime"):
            return self._dates
        if level in (1, "instrument"):
            return self._instruments
        raise KeyError(level)


class FakeFrame:
    def __init__(self, columns, values, dates, instruments):
        self.columns = list(columns)
        self._values = np.asarray(values, dtype=np.float64)
        self.index = FakeIndex(dates, instruments)
        self.empty = self._values.shape[0] == 0

    def to_numpy(self):
        return self._values


class FakePrepared:
    def __init__(self, feature_frame, label_frame):
        self.index = feature_frame.index
        self._groups = {"feature": feature_frame, "label": label_frame}

    def __getitem__(self, name):
        return self._groups[name]


class FakeDatasetH:
    def __init__(self, training, inference):
        self.training = training
        self.inference = inference
        self.calls = []

    def prepare(self, segment, *, col_set, data_key):
        self.calls.append(
            {"segment": segment, "col_set": col_set, "data_key": data_key}
        )
        if segment == "train":
            return self.training
        return self.inference


class FakeLightGBMDataset:
    def __init__(self, data, label, feature_name):
        self.data = np.asarray(data)
        self.label = np.asarray(label)
        self.feature_name = list(feature_name)


class FakeBooster:
    def __init__(self):
        self.prediction_calls = []

    def predict(self, matrix):
        self.prediction_calls.append(np.asarray(matrix))
        return [0.05 + 0.01 * index for index in range(len(matrix))]


def _admission():
    return TrainingDatasetAdmission(
        admitted=True,
        report_id="synthetic-report",
        start_date="2024-01-01",
        end_date="2024-02-01",
        universe_profile="synthetic",
        training_cutoff="2024-01-15",
        feature_rows=2,
        label_rows=2,
        feature_columns=("return_5", "pit_roe"),
        label_horizon_sessions=5,
        source_fingerprint="a" * 64,
        feature_fingerprint="b" * 64,
        label_fingerprint="c" * 64,
        rejection_reasons=(),
    )


def _dataset(admission):
    train_index = FakeIndex(
        ["2024-01-05", "2024-01-12"],
        ["000001.SZ", "000002.SZ"],
    )
    train_features = FakeFrame(
        ["return_5", "pit_roe"],
        [[0.02, 0.10], [-0.01, 0.12]],
        ["2024-01-05", "2024-01-12"],
        ["000001.SZ", "000002.SZ"],
    )
    train_labels = FakeFrame(
        ["forward_return"],
        [[0.03], [0.01]],
        ["2024-01-05", "2024-01-12"],
        ["000001.SZ", "000002.SZ"],
    )
    # Both frames share the same row index, matching Qlib's group selection.
    train_labels.index = train_index
    train_features.index = train_index
    training = FakePrepared(train_features, train_labels)

    inference = FakeFrame(
        ["return_5", "pit_roe"],
        [[0.04, 0.15], [0.03, 0.08]],
        ["2024-01-19", "2024-01-19"],
        ["000003.SZ", "600000.SH"],
    )
    qlib_dataset = FakeDatasetH(training, inference)
    return QlibDatasetBundle(
        dataset=qlib_dataset,
        admission=admission,
        feature_columns=("return_5", "pit_roe"),
        segment_bounds={
            "train": ("2024-01-01", "2024-01-12"),
            "valid": ("2024-01-19", "2024-01-19"),
        },
    )


def _install_qlib(monkeypatch):
    handler = types.ModuleType("qlib.data.dataset.handler")
    handler.DataHandlerLP = types.SimpleNamespace(DK_L="learn", DK_I="infer")
    modules = {
        "qlib": types.ModuleType("qlib"),
        "qlib.data": types.ModuleType("qlib.data"),
        "qlib.data.dataset": types.ModuleType("qlib.data.dataset"),
        "qlib.data.dataset.handler": handler,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def _install_lightgbm(monkeypatch, captured):
    lightgbm = types.ModuleType("lightgbm")
    lightgbm.Dataset = FakeLightGBMDataset

    def train(params, dataset, num_boost_round):
        captured["params"] = params
        captured["dataset"] = dataset
        captured["num_boost_round"] = num_boost_round
        captured["booster"] = FakeBooster()
        return captured["booster"]

    lightgbm.train = train
    monkeypatch.setitem(sys.modules, "lightgbm", lightgbm)


def test_qlib_dataset_prepare_feeds_lightgbm_and_returns_keyed_predictions(monkeypatch):
    captured = {}
    _install_qlib(monkeypatch)
    _install_lightgbm(monkeypatch, captured)
    admission = _admission()
    dataset = _dataset(admission)

    model = fit_qlib_lightgbm_regressor(
        dataset=dataset,
        admission=admission,
        config=LightGBMConfig(min_training_rows=2, num_boost_round=3),
    )

    assert model.training_rows == 2
    assert model.feature_columns == ("return_5", "pit_roe")
    assert captured["dataset"].data.tolist() == [[0.02, 0.10], [-0.01, 0.12]]
    assert captured["dataset"].label.tolist() == [0.03, 0.01]
    assert captured["dataset"].feature_name == ["return_5", "pit_roe"]
    assert captured["num_boost_round"] == 3
    assert dataset.dataset.calls == [
        {
            "segment": "train",
            "col_set": ["feature", "label"],
            "data_key": "learn",
        }
    ]

    predictions = predict_qlib_lightgbm_regressor(
        model=model,
        dataset=dataset,
        segment="valid",
    )
    rows = predictions.to_dicts()
    assert [(row["signal_date"], row["instrument"]) for row in rows] == [
        ("2024-01-19", "000003.SZ"),
        ("2024-01-19", "600000.SH"),
    ]
    assert [row["predicted_return"] for row in rows] == pytest.approx([0.05, 0.06])
    assert dataset.dataset.calls[-1] == {
        "segment": "valid",
        "col_set": "feature",
        "data_key": "infer",
    }


def test_qlib_training_rejects_admission_mismatch(monkeypatch):
    _install_qlib(monkeypatch)
    admission = _admission()
    dataset = _dataset(admission)
    changed = replace(admission, report_id="different-report")

    with pytest.raises(ValueError, match="does not match"):
        fit_qlib_lightgbm_regressor(
            dataset=dataset,
            admission=changed,
            config=LightGBMConfig(min_training_rows=2),
        )


def test_qlib_training_rejects_cutoff_or_row_count_mismatch(monkeypatch):
    _install_qlib(monkeypatch)
    admission = _admission()
    original = _dataset(admission)
    dataset = QlibDatasetBundle(
        dataset=original.dataset,
        admission=admission,
        feature_columns=original.feature_columns,
        segment_bounds={
            "train": ("2024-01-01", "2024-01-15"),
            "valid": ("2024-01-19", "2024-01-19"),
        },
    )
    with pytest.raises(ValueError, match="cutoff"):
        fit_qlib_lightgbm_regressor(
            dataset=dataset,
            admission=admission,
            config=LightGBMConfig(min_training_rows=2),
        )


def test_qlib_prediction_rejects_training_segment(monkeypatch):
    captured = {}
    _install_qlib(monkeypatch)
    _install_lightgbm(monkeypatch, captured)
    admission = _admission()
    dataset = _dataset(admission)
    model = fit_qlib_lightgbm_regressor(
        dataset=dataset,
        admission=admission,
        config=LightGBMConfig(min_training_rows=2),
    )
    with pytest.raises(ValueError, match="non-train"):
        predict_qlib_lightgbm_regressor(
            model=model,
            dataset=dataset,
            segment="train",
        )
