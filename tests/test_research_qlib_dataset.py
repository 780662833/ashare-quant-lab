from __future__ import annotations

import sys
import types

import polars as pl
import pytest

from ashare_quant.research.qlib_dataset import (
    build_qlib_panel_rows,
    create_qlib_dataset,
)


def _frames():
    features = pl.DataFrame(
        {
            "signal_date": ["2024-01-05", "2024-01-12"],
            "instrument": ["000001.SZ", "000002.SZ"],
            "return_5": [0.02, -0.01],
        }
    )
    labels = pl.DataFrame(
        {
            "signal_date": ["2024-01-05", "2024-01-12"],
            "instrument": ["000001.SZ", "000002.SZ"],
            "label_end_date": ["2024-01-12", "2024-01-19"],
            "forward_return": [0.03, 0.01],
        }
    )
    from ashare_quant.research.admission import TrainingDatasetAdmission, _frame_fingerprint

    admission = TrainingDatasetAdmission(
        admitted=True,
        report_id="synthetic-report",
        start_date="2024-01-01",
        end_date="2024-02-01",
        universe_profile="synthetic",
        training_cutoff="2024-01-22",
        feature_rows=2,
        label_rows=2,
        feature_columns=("return_5",),
        label_horizon_sessions=5,
        source_fingerprint="a" * 64,
        feature_fingerprint=_frame_fingerprint(features, keys=["signal_date", "instrument"]),
        label_fingerprint=_frame_fingerprint(labels, keys=["signal_date", "instrument"]),
        rejection_reasons=(),
    )
    return features, labels, admission


def test_build_qlib_panel_rows_preserves_keys_features_and_mature_labels():
    features, labels, admission = _frames()
    keys, columns, targets = build_qlib_panel_rows(
        features=features, labels=labels, admission=admission
    )
    assert keys == [
        ("2024-01-05", "000001.SZ"),
        ("2024-01-12", "000002.SZ"),
    ]
    assert columns == ["return_5"]
    assert targets == [0.03, 0.01]


def test_build_qlib_panel_rows_rejects_changed_feature_frame():
    features, labels, admission = _frames()
    changed = features.with_columns(pl.lit(9.0).alias("return_5"))
    with pytest.raises(ValueError, match="fingerprint"):
        build_qlib_panel_rows(features=changed, labels=labels, admission=admission)


def test_build_qlib_panel_rows_rejects_unadmitted_data():
    features, labels, admission = _frames()
    from dataclasses import replace

    with pytest.raises(ValueError, match="admitted"):
        build_qlib_panel_rows(
            features=features, labels=labels, admission=replace(admission, admitted=False)
        )


def test_create_qlib_dataset_uses_lazy_optional_api(monkeypatch):
    features, labels, admission = _frames()
    captured = {}

    class FakeMultiIndex:
        @staticmethod
        def from_arrays(values, names=None):
            captured["index"] = (values, names)
            return values

        @staticmethod
        def from_tuples(values, names=None):
            captured["columns"] = values
            return values

    class FakeColumns:
        @staticmethod
        def from_tuples(values):
            captured["columns"] = values
            return values

    class FakeFrame:
        def __init__(self, values, index, columns):
            captured["values"] = values
            captured["index_object"] = index
            captured["column_object"] = columns

    class FakeHandler:
        @classmethod
        def from_df(cls, frame):
            captured["frame"] = frame
            return "handler"

    class FakeDataset:
        def __init__(self, *, handler, segments):
            self.handler = handler
            self.segments = segments

    pandas = types.ModuleType("pandas")
    pandas.MultiIndex = FakeMultiIndex
    pandas.DataFrame = FakeFrame
    pandas.to_datetime = lambda values: values
    qlib = types.ModuleType("qlib")
    qlib_data = types.ModuleType("qlib.data")
    qlib_dataset = types.ModuleType("qlib.data.dataset")
    qlib_handler = types.ModuleType("qlib.data.dataset.handler")
    qlib_dataset.DatasetH = FakeDataset
    qlib_handler.DataHandlerLP = FakeHandler
    for name, module in {
        "pandas": pandas,
        "qlib": qlib,
        "qlib.data": qlib_data,
        "qlib.data.dataset": qlib_dataset,
        "qlib.data.dataset.handler": qlib_handler,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    inference_features = pl.DataFrame(
        {
            "signal_date": ["2024-01-19"],
            "instrument": ["000003.SZ"],
            "return_5": [0.04],
        }
    )
    result = create_qlib_dataset(
        features=features,
        labels=labels,
        admission=admission,
        segments={
            "train": ("2024-01-01", "2024-01-12"),
            "valid": ("2024-01-19", "2024-01-19"),
        },
        inference_features=inference_features,
    )
    assert result.handler == "handler"
    assert result.segments == {
        "train": ("2024-01-01", "2024-01-12"),
        "valid": ("2024-01-19", "2024-01-19"),
    }
    assert result.admission == admission
    assert result.feature_columns == ("return_5",)
    assert captured["index"][1] == ["datetime", "instrument"]
    assert captured["index"][0][0] == ["2024-01-05", "2024-01-12", "2024-01-19"]
    assert captured["index"][0][1] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert captured["columns"] == [("feature", "return_5"), ("label", "forward_return")]
    assert captured["values"][:2] == [[0.02, 0.03], [-0.01, 0.01]]
    assert captured["values"][2][0] == 0.04
    assert captured["values"][2][1] != captured["values"][2][1]



def test_build_qlib_panel_rows_allows_feature_rows_without_mature_labels():
    from dataclasses import replace
    from ashare_quant.research.admission import _frame_fingerprint

    features, labels, admission = _frames()
    features = pl.concat(
        [
            features,
            pl.DataFrame(
                {
                    "signal_date": ["2024-01-19"],
                    "instrument": ["000003.SZ"],
                    "return_5": [0.04],
                }
            ),
        ]
    )
    admission = replace(
        admission,
        feature_rows=3,
        feature_fingerprint=_frame_fingerprint(
            features, keys=["signal_date", "instrument"]
        ),
    )
    keys, columns, targets = build_qlib_panel_rows(
        features=features, labels=labels, admission=admission
    )
    assert keys == [
        ("2024-01-05", "000001.SZ"),
        ("2024-01-12", "000002.SZ"),
    ]
    assert columns == ["return_5"]
    assert targets == [0.03, 0.01]

def test_create_qlib_dataset_rejects_overlapping_segments():
    features, labels, admission = _frames()
    with pytest.raises(ValueError, match="overlap"):
        create_qlib_dataset(
            features=features,
            labels=labels,
            admission=admission,
            segments={
                "train": ("2024-01-01", "2024-01-12"),
                "valid": ("2024-01-12", "2024-01-19"),
            },
        )
