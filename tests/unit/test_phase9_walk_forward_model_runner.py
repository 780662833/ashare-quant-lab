from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from ashare_quant.backtest import BacktestConfig
from ashare_quant.research.bundle import build_research_data_bundle
from ashare_quant.research.cnequity_assembler import bind_pit_fundamental_provenance
from ashare_quant.research.features import (
    CrossSectionalFeatureConfig,
    build_cross_sectional_features,
)
from ashare_quant.research.labels import build_forward_return_labels
from ashare_quant.research.lightgbm_model import LightGBMConfig
from ashare_quant.research.walk_forward import WalkForwardConfig
from ashare_quant.strategies.momentum import MomentumConfig
from ashare_quant.research.walk_forward_model_runner import (
    run_walk_forward_cross_sectional_model,
)
from ashare_quant.research.model_experiment_store import (
    persist_walk_forward_model_run,
    verify_walk_forward_model_run,
)

PROFILE = "cn_a_sh_sz_research_v1"


class FakeDataset:
    def __init__(self, data, label, feature_name):
        self.data = data
        self.label = label
        self.feature_name = feature_name


class FakeBooster:
    def predict(self, matrix):
        return [0.02 + index * 0.001 for index in range(len(matrix))]

    def save_model(self, path):
        from pathlib import Path
        Path(path).write_text("synthetic-fake-booster", encoding="utf-8")


def fake_lightgbm(monkeypatch):
    import sys
    monkeypatch.setitem(
        sys.modules,
        "lightgbm",
        SimpleNamespace(
            Dataset=FakeDataset,
            train=lambda params, dataset, num_boost_round: FakeBooster(),
        ),
    )


def make_bundle_inputs(count=70):
    sessions = []
    current = date(2024, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)

    symbols = (("600000.SH", 10.0, 0.08), ("000001.SZ", 20.0, 0.05), ("600519.SH", 30.0, 0.03))
    raw, adjusted, state = [], [], []
    for i, session in enumerate(sessions):
        for symbol, base, slope in symbols:
            close = base + slope * i
            raw.append({
                "instrument": symbol, "date": session, "open": close,
                "high": close * 1.01, "low": close * 0.99, "close": close,
                "volume": 100_000 + i * 100, "amount": close * (100_000 + i * 100),
            })
            adjusted.append({
                "instrument": symbol, "date": session, "adj_close": close,
                "adj_is_exact": True,
            })
            state.append({
                "instrument": symbol, "date": session, "is_suspended": False,
                "is_limit_up_locked": False, "is_limit_down_locked": False,
                "limit_up": close * 1.20, "limit_down": close * 0.80,
                "risk_warning_known": True,
                "limit_evidence_source": "synthetic_rule_provider",
                "limit_rule_version": "synthetic-rules-v1",
                "limit_regime": "synthetic_limited_session",
            })

    weekly = {}
    for session in sessions:
        iso = date.fromisoformat(session).isocalendar()
        weekly[(iso.year, iso.week)] = session
    membership = [
        {"instrument": symbol, "date": signal_date}
        for signal_date in sorted(weekly.values())
        for symbol, _, _ in symbols
    ]
    window = {"start": sessions[0], "end": sessions[-1]}
    pit_revision = {"revision_id": "synthetic-pit-v1", "contract_fingerprint": "synthetic-pit-contract"}
    report = {
        "report_id": "synthetic-phase9-acceptance-v1",
        "created_at": "2026-11-14T00:00:00Z",
        "overall_status": "PASS",
        "window": window,
        "profile": PROFILE,
        "pit_items": ["roe", "net_profit"],
        "training_dataset_unlocked": False,
        "gates": [
            {"name": "historical_universe", "status": "PASS",
             "supported_window": {"start": sessions[0], "end": sessions[-1]}},
            {"name": "historical_universe_receipt", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "PASS", "diagnostics_assessed": True,
             "missing_factor_rows": 0, "related_historical_events": 0},
            {"name": "point_in_time_fundamentals", "status": "PASS", "mode": "strict",
             "items": ["roe", "net_profit"], "missing_symbol_item_pairs": 0},
            {"name": "trading_status", "status": "PASS", "missing_status_rows": 0,
             "unknown_risk_warning_rows": 0, "contradictory_rows": 0,
             "excluded_or_unresolved_rows": 0},
        ],
        "receipts": {
            "historical_universe": {"receipt_id": "synthetic-universe", "replayable": True},
            "hfq": {"receipt_id": "synthetic-hfq", "replayable": True},
            "pit": {"receipt_id": "synthetic-pit", "replayable": True,
                   "dependencies": {"financial_statement_items": pit_revision}},
        },
    }
    dependencies = {
        "daily_bars": {"revision_id": "daily-v1", "contract_fingerprint": "daily-contract"},
        "adj_factors": {"revision_id": "hfq-v1", "contract_fingerprint": "hfq-contract"},
        "trading_status": {"revision_id": "status-v1", "contract_fingerprint": "status-contract"},
        "trading_calendar": {"revision_id": "calendar-v1", "contract_fingerprint": "calendar-contract"},
    }
    revisions = {
        **dependencies,
        "universe": dependencies["daily_bars"],
        "price_limit_rules": {
            "revision_id": "synthetic-rules-v1",
            "contract_fingerprint": "synthetic-rules-contract",
            "source": "synthetic_rule_provider",
        },
    }
    receipts = [{
        "dataset": "daily_bars",
        "receipt": {
            "replayable": True,
            "receipt_id": "synthetic-adapter-receipt",
            "dependencies": dependencies,
            "coverage": {"completeness": "audited"},
        },
    }]
    counts = {
        "daily_bars": len(raw),
        "adj_factors": len(adjusted),
        "universe": len(membership),
        "trading_status": len(state),
        "trading_calendar": len(sessions),
        "price_limit_rules": len(state),
    }
    audits = {
        name: {
            "audit_id": f"synthetic-{name}-audit",
            "status": "PASS",
            "window": window,
            "expected_rows": rows,
            "observed_rows": rows,
            "missing_keys": 0,
            "unexpected_keys": 0,
            "method": "synthetic exact-key reconciliation",
            "source": "synthetic fixture only",
        }
        for name, rows in counts.items()
    }
    return {
        "raw_bars": pl.DataFrame(raw),
        "adjusted_bars": pl.DataFrame(adjusted),
        "universe_membership": pl.DataFrame(membership),
        "execution_evidence": pl.DataFrame(state),
        "trading_dates": sessions,
        "acceptance_report": report,
        "acceptance_report_id": report["report_id"],
        "source_revisions": revisions,
        "source_receipts": receipts,
        "coverage_audits": audits,
        "start_date": sessions[0],
        "end_date": sessions[-1],
        "universe_profile": PROFILE,
    }


def test_walk_forward_model_runner_keeps_each_fold_admitted_and_test_only(monkeypatch, tmp_path):
    fake_lightgbm(monkeypatch)

    # Exercise the existing fold orchestration without requiring a real Qlib install.
    # The Qlib -> LightGBM adapters have separate contract tests.
    import ashare_quant.research.walk_forward_model_runner as runner_module
    from ashare_quant.research.lightgbm_model import fit_lightgbm_regressor

    def fake_create_qlib_dataset(
        *, features, labels, admission, segments, inference_features=None
    ):
        return SimpleNamespace(
            features=features,
            labels=labels,
            admission=admission,
            segment_bounds=dict(segments),
            inference_features=inference_features,
        )

    def fake_fit_qlib(*, dataset, admission, config=None):
        assert dataset.admission == admission
        return fit_lightgbm_regressor(
            features=dataset.features,
            labels=dataset.labels,
            admission=admission,
            config=config,
        )

    def fake_predict_qlib(*, model, dataset, segment):
        assert segment in dataset.segment_bounds
        return model.predict(dataset.inference_features)

    monkeypatch.setattr(runner_module, "create_qlib_dataset", fake_create_qlib_dataset)
    monkeypatch.setattr(runner_module, "fit_qlib_lightgbm_regressor", fake_fit_qlib)
    monkeypatch.setattr(runner_module, "predict_qlib_lightgbm_regressor", fake_predict_qlib)

    inputs = make_bundle_inputs()
    report = inputs["acceptance_report"]
    bundle = build_research_data_bundle(**inputs)
    pit_revision = {"revision_id": "synthetic-pit-v1", "contract_fingerprint": "synthetic-pit-contract"}
    pit_dependencies = {
        **bundle.provenance["receipt_provenance"]["dependency_identities"],
        "financial_statement_items": pit_revision,
    }
    bind_pit_fundamental_provenance(
        bundle,
        items=["roe", "net_profit"],
        as_of_date=report["window"]["end"],
        receipt={
            "replayable": True,
            "receipt_id": "synthetic-pit-adapter-receipt",
            "dependencies": pit_dependencies,
            "coverage": {"completeness": "audited"},
        },
        revision_identity=pit_revision,
        row_count=6,
    )

    fundamentals = pl.DataFrame([
        {
            "instrument": symbol,
            "item": item,
            "report_period": "2023-09-30",
            "available_at": "2023-10-30T10:00:00+08:00",
            "value": value,
            "pit_quality": "strict",
            "pit_is_exact": True,
        }
        for symbol, _, _ in (("600000.SH", 10.0, 0.08), ("000001.SZ", 20.0, 0.05), ("600519.SH", 30.0, 0.03))
        for item, value in (("roe", 0.12), ("net_profit", 100.0))
    ])
    feature_config = CrossSectionalFeatureConfig(
        return_windows=(5, 10), volatility_window=5, activity_window=5
    )
    features = build_cross_sectional_features(
        adjusted_bars=bundle.adjusted_bars,
        execution_bars=bundle.execution_bars,
        universe_membership=bundle.universe_membership,
        fundamentals=fundamentals,
        trading_dates=bundle.trading_dates,
        config=feature_config,
    )
    labels = build_forward_return_labels(
        adjusted_bars=bundle.adjusted_bars,
        universe_membership=bundle.universe_membership,
        trading_dates=bundle.trading_dates,
        horizon_sessions=5,
    )

    walk_config = WalkForwardConfig(
        min_train_sessions=20, validation_sessions=5, test_sessions=10,
        step_sessions=10, label_horizon_sessions=5, embargo_sessions=1,
    )
    learner_config = LightGBMConfig(
        num_boost_round=2, min_training_rows=2, num_leaves=3, min_data_in_leaf=1,
    )
    execution_config = BacktestConfig(initial_cash=100_000, top_n=2)
    momentum_config = MomentumConfig(top_n=2, lookback_sessions=8, skip_recent_sessions=2)
    result = run_walk_forward_cross_sectional_model(
        bundle,
        acceptance_report=report,
        features=features,
        labels=labels,
        walk_forward_config=walk_config,
        model_config=learner_config,
        backtest_config=execution_config,
        top_n=2,
        benchmark_config=momentum_config,
    )

    assert len(result.folds) >= 2
    assert result.aggregate_report["model_training_performed"] is True
    assert result.aggregate_report["training_dataset_unlocked"] is False
    for fold in result.folds:
        assert fold.training_admission.admitted is True
        assert fold.final_admission.admitted is True
        assert fold.final_admission.training_cutoff == fold.fold.test_start
        assert fold.test_predictions.height > 0
        assert fold.targets.height > 0
        assert fold.daily.height == fold.fold.test_sessions
        assert fold.daily["date"].min() == fold.fold.test_start
        assert fold.daily["date"].max() == fold.fold.test_end
        assert fold.provenance["walk_forward"]["model_training_performed"] is True
        assert fold.benchmark_report["model_training_performed"] is False
        assert fold.benchmark_daily.height == fold.daily.height
        assert fold.benchmark_report["initial_nav"] == fold.report["initial_nav"]
    assert result.oos_daily.height == sum(item.fold.test_sessions for item in result.folds)
    assert result.benchmark_oos_daily.height == result.oos_daily.height
    assert "mean_fold_excess_return" in result.aggregate_report

    saved = persist_walk_forward_model_run(
        result,
        root=tmp_path / "phase9-model-run",
        experiment_id="phase9-synthetic-run",
        repository_commit="a" * 40,
        walk_forward_config=walk_config,
        model_config=learner_config,
        backtest_config=execution_config,
        benchmark_config=momentum_config,
        top_n=2,
    )
    verified = verify_walk_forward_model_run(saved)
    assert verified["verified"] is True
    assert verified["fold_count"] == len(result.folds)
    manifest = verified["manifest"]
    artifact = saved / manifest["artifacts"][0]["path"]
    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_walk_forward_model_run(saved)


def test_walk_forward_model_runner_rejects_bundle_acceptance_identity_mismatch(monkeypatch):
    inputs = make_bundle_inputs()
    bundle = build_research_data_bundle(**inputs)
    report = {**inputs["acceptance_report"], "report_id": "wrong-report"}
    with pytest.raises(ValueError, match="identity"):
        run_walk_forward_cross_sectional_model(
            bundle,
            acceptance_report=report,
            features=pl.DataFrame({"signal_date": [], "instrument": []}),
            labels=pl.DataFrame({
                "signal_date": [], "instrument": [], "label_end_date": [],
                "forward_return": [],
            }),
            walk_forward_config=WalkForwardConfig(
                min_train_sessions=20, validation_sessions=5, test_sessions=10,
                label_horizon_sessions=5, embargo_sessions=1,
            ),
        )



def test_real_qlib_walk_forward_pipeline_on_synthetic_bundle(monkeypatch):
    """Run the full model walk-forward orchestration through real Qlib APIs.

    LightGBM Dataset objects and Qlib are real. Only lightgbm.train is replaced
    with a prediction-only Booster, so no optimization/model fit occurs.
    """
    lgb = pytest.importorskip("lightgbm", reason="install optional LightGBM")
    pytest.importorskip("pandas", reason="Qlib requires pandas")
    qlib_handler = pytest.importorskip(
        "qlib.data.dataset.handler",
        reason="install optional Qlib 0.9.7",
    )
    DataHandlerLP = qlib_handler.DataHandlerLP
    assert DataHandlerLP.DK_L is not None
    assert DataHandlerLP.DK_I is not None

    captured = []

    class PredictionOnlyBooster:
        def predict(self, matrix):
            return [0.02 + index * 0.001 for index in range(len(matrix))]

    def no_fit(params, dataset, num_boost_round):
        # Construct the real LightGBM Dataset to test the real feature/label
        # bridge; do not invoke LightGBM's optimizer.
        dataset.construct()
        captured.append(
            {
                "params": dict(params),
                "rows": dataset.num_data(),
                "features": dataset.num_feature(),
                "feature_names": dataset.get_feature_name(),
                "num_boost_round": num_boost_round,
            }
        )
        return PredictionOnlyBooster()

    monkeypatch.setattr(lgb, "train", no_fit)

    inputs = make_bundle_inputs()
    report = inputs["acceptance_report"]
    bundle = build_research_data_bundle(**inputs)
    pit_revision = {
        "revision_id": "synthetic-pit-v1",
        "contract_fingerprint": "synthetic-pit-contract",
    }
    pit_dependencies = {
        **bundle.provenance["receipt_provenance"]["dependency_identities"],
        "financial_statement_items": pit_revision,
    }
    bind_pit_fundamental_provenance(
        bundle,
        items=["roe", "net_profit"],
        as_of_date=report["window"]["end"],
        receipt={
            "replayable": True,
            "receipt_id": "synthetic-pit-adapter-receipt",
            "dependencies": pit_dependencies,
            "coverage": {"completeness": "audited"},
        },
        revision_identity=pit_revision,
        row_count=6,
    )

    fundamentals = pl.DataFrame(
        [
            {
                "instrument": symbol,
                "item": item,
                "report_period": "2023-09-30",
                "available_at": "2023-10-30T10:00:00+08:00",
                "value": value,
                "pit_quality": "strict",
                "pit_is_exact": True,
            }
            for symbol, _, _ in (
                ("600000.SH", 10.0, 0.08),
                ("000001.SZ", 20.0, 0.05),
                ("600519.SH", 30.0, 0.03),
            )
            for item, value in (("roe", 0.12), ("net_profit", 100.0))
        ]
    )
    features = build_cross_sectional_features(
        adjusted_bars=bundle.adjusted_bars,
        execution_bars=bundle.execution_bars,
        universe_membership=bundle.universe_membership,
        fundamentals=fundamentals,
        trading_dates=bundle.trading_dates,
        config=CrossSectionalFeatureConfig(
            return_windows=(5, 10),
            volatility_window=5,
            activity_window=5,
        ),
    )
    labels = build_forward_return_labels(
        adjusted_bars=bundle.adjusted_bars,
        universe_membership=bundle.universe_membership,
        trading_dates=bundle.trading_dates,
        horizon_sessions=5,
    )

    walk_config = WalkForwardConfig(
        min_train_sessions=20,
        validation_sessions=5,
        test_sessions=10,
        step_sessions=10,
        label_horizon_sessions=5,
        embargo_sessions=1,
    )
    learner_config = LightGBMConfig(
        num_boost_round=2,
        min_training_rows=2,
        num_leaves=3,
        min_data_in_leaf=1,
    )
    execution_config = BacktestConfig(initial_cash=100_000, top_n=2)
    momentum_config = MomentumConfig(
        top_n=2,
        lookback_sessions=8,
        skip_recent_sessions=2,
    )

    result = run_walk_forward_cross_sectional_model(
        bundle,
        acceptance_report=report,
        features=features,
        labels=labels,
        walk_forward_config=walk_config,
        model_config=learner_config,
        backtest_config=execution_config,
        top_n=2,
        benchmark_config=momentum_config,
    )

    assert len(result.folds) >= 2
    assert len(captured) == len(result.folds) * 2
    assert all(call["rows"] >= 2 for call in captured)
    assert all(call["features"] == len(features.columns) - 2 for call in captured)
    assert all(call["feature_names"] for call in captured)
    assert all(call["num_boost_round"] == 2 for call in captured)

    for fold in result.folds:
        assert fold.training_admission.admitted
        assert fold.final_admission.admitted
        assert fold.final_admission.training_cutoff == fold.fold.test_start
        assert fold.final_training_rows == fold.final_admission.label_rows
        assert fold.validation_predictions.height > 0
        assert fold.test_predictions.height > 0
        assert fold.targets.height > 0
        assert fold.targets.group_by("signal_date").agg(
            pl.col("target_weight").sum().alias("weight_sum")
        )["weight_sum"].to_list() == pytest.approx([1.0] * fold.targets["signal_date"].n_unique())
        assert fold.daily.height == fold.fold.test_sessions
        assert fold.daily["date"].min() == fold.fold.test_start
        assert fold.daily["date"].max() == fold.fold.test_end
        assert fold.benchmark_daily.height == fold.daily.height
        assert fold.benchmark_report["initial_nav"] == fold.report["initial_nav"]
        assert fold.provenance["walk_forward"]["model_training_performed"] is True

    assert result.oos_daily.height == sum(item.fold.test_sessions for item in result.folds)
    assert result.benchmark_oos_daily.height == result.oos_daily.height
    assert result.aggregate_report["training_dataset_unlocked"] is False
    assert result.aggregate_report["model_training_performed"] is True
