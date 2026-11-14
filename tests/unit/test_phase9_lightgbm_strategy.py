from types import SimpleNamespace

import polars as pl
import pytest

from ashare_quant.research.admission import TrainingDatasetAdmission, _frame_fingerprint
from ashare_quant.research.lightgbm_model import (
    LightGBMConfig,
    LightGBMRegressionModel,
    fit_lightgbm_regressor,
)
from ashare_quant.strategies.cross_sectional import weekly_cross_sectional_model_targets


class FakeDataset:
    def __init__(self, data, label, feature_name):
        self.data = data
        self.label = label
        self.feature_name = feature_name


class FakeBooster:
    def __init__(self, model_file=None):
        self.model_file = model_file

    def predict(self, matrix):
        return [0.05 + index * 0.01 for index in range(len(matrix))]

    def save_model(self, path):
        from pathlib import Path
        Path(path).write_text("fake-lightgbm-booster", encoding="utf-8")


def fake_lightgbm(monkeypatch):
    import sys
    fake = SimpleNamespace(
        Dataset=FakeDataset,
        train=lambda params, dataset, num_boost_round: FakeBooster(),
        Booster=FakeBooster,
    )
    monkeypatch.setitem(sys.modules, "lightgbm", fake)


def frames():
    features = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05", "2024-01-12"],
        "instrument": ["600000.SH", "000001.SZ", "600000.SH"],
        "return_5": [0.01, 0.02, 0.03],
        "pit_roe": [0.1, 0.2, 0.1],
    })
    labels = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "000001.SZ"],
        "label_end_date": ["2024-01-12", "2024-01-12"],
        "forward_return": [0.03, -0.01],
    })
    return features, labels


def admitted(features, labels):
    return TrainingDatasetAdmission(
        admitted=True,
        report_id="acceptance-fixture",
        start_date="2024-01-01",
        end_date="2024-04-30",
        universe_profile="cn_a_sh_sz_research_v1",
        training_cutoff="2024-01-15",
        feature_rows=features.height,
        label_rows=labels.height,
        feature_columns=("return_5", "pit_roe"),
        label_horizon_sessions=5,
        source_fingerprint="a" * 64,
        feature_fingerprint=_frame_fingerprint(features, keys=["signal_date", "instrument"]),
        label_fingerprint=_frame_fingerprint(labels, keys=["signal_date", "instrument"]),
        rejection_reasons=(),
    )


def test_fit_requires_successful_admission_and_exact_admitted_frames(monkeypatch):
    features, labels = frames()
    fake_lightgbm(monkeypatch)
    decision = admitted(features, labels)
    model = fit_lightgbm_regressor(
        features=features, labels=labels, admission=decision,
        config=LightGBMConfig(min_training_rows=2),
    )
    assert model.training_rows == 2
    assert model.feature_columns == ("return_5", "pit_roe")

    with pytest.raises(PermissionError, match="successful"):
        fit_lightgbm_regressor(
            features=features, labels=labels,
            admission=TrainingDatasetAdmission(**{
                **decision.to_dict(), "admitted": False,
                "rejection_reasons": ("gate failed",),
            }),
            config=LightGBMConfig(min_training_rows=2),
        )
    changed = features.with_columns((pl.col("return_5") + 1).alias("return_5"))
    with pytest.raises(ValueError, match="fingerprint"):
        fit_lightgbm_regressor(
            features=changed, labels=labels, admission=decision,
            config=LightGBMConfig(min_training_rows=2),
        )


def test_model_predicts_only_complete_rows_and_returns_model_scores(monkeypatch):
    fake_lightgbm(monkeypatch)
    features, labels = frames()
    model = LightGBMRegressionModel(
        booster=FakeBooster(),
        feature_columns=("return_5", "pit_roe"),
        config=LightGBMConfig(min_training_rows=2),
        acceptance_report_id="acceptance-fixture",
        training_cutoff="2024-01-15",
        training_rows=2,
        source_fingerprint="a" * 64,
        feature_fingerprint="b" * 64,
        label_fingerprint="c" * 64,
    )
    prediction_input = pl.concat([
        features,
        pl.DataFrame({
            "signal_date": ["2024-01-19"],
            "instrument": ["600000.SH"],
            "return_5": [None],
            "pit_roe": [0.2],
        }),
    ])
    result = model.predict(prediction_input)
    assert result.columns == ["signal_date", "instrument", "predicted_return"]
    assert result.height == 3


def test_model_save_load_checks_sidecar_checksum(tmp_path, monkeypatch):
    fake_lightgbm(monkeypatch)
    model = LightGBMRegressionModel(
        booster=FakeBooster(),
        feature_columns=("return_5", "pit_roe"),
        config=LightGBMConfig(min_training_rows=2),
        acceptance_report_id="acceptance-fixture",
        training_cutoff="2024-01-15",
        training_rows=2,
        source_fingerprint="a" * 64,
        feature_fingerprint="b" * 64,
        label_fingerprint="c" * 64,
    )
    path = model.save(tmp_path / "model.txt")
    loaded = LightGBMRegressionModel.load(path)
    assert loaded.feature_columns == model.feature_columns
    assert loaded.acceptance_report_id == model.acceptance_report_id
    assert loaded.training_cutoff == model.training_cutoff
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        LightGBMRegressionModel.load(path)


def test_model_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="positive integer"):
        LightGBMConfig(num_boost_round=0)
    with pytest.raises(ValueError, match="learning_rate"):
        LightGBMConfig(learning_rate=0)


def test_weekly_model_strategy_selects_top_n_equal_weight_with_stable_ties():
    predictions = pl.DataFrame({
        "signal_date": ["2024-01-05"] * 3,
        "instrument": ["600000.SH", "000001.SZ", "600519.SH"],
        "predicted_return": [0.2, 0.2, 0.1],
    })
    result = weekly_cross_sectional_model_targets(predictions, top_n=2)
    assert result["instrument"].to_list() == ["000001.SZ", "600000.SH"]
    assert result["weight"].to_list() == [0.5, 0.5]


def test_weekly_model_strategy_rejects_missing_prediction_contract():
    with pytest.raises(ValueError, match="missing columns"):
        weekly_cross_sectional_model_targets(pl.DataFrame({"score": [0.1]}))
