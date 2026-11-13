from pathlib import Path

import polars as pl
import pytest

from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment
from ashare_quant.research import fill_stress

def make_experiment(tmp_path, *, fail_gate=False, orders=None):
    dependencies = {
        "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
        "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
        "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
        "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
    }
    revisions = {
        **dependencies, "universe": dependencies["daily_bars"],
        "price_limit_rules": {
            "revision_id": "limits-r1", "contract_fingerprint": "limits-contract",
            "source": "synthetic-provider",
        },
    }
    provenance = {
        "acceptance_report_id": "acceptance-fill-001",
        "acceptance_report_window": {"start": "2020-01-01", "end": "2020-12-31"},
        "universe_profile": "cn_a_sh_sz_research_v1",
        "acceptance_gates": {
            "historical_universe": "FAIL" if fail_gate else "PASS",
            "hfq_adjustment": "PASS",
            "point_in_time_fundamentals": "FAIL",
            "trading_status": "PASS",
        },
        "required_benchmark_gates": {
            "historical_universe": "FAIL" if fail_gate else "PASS",
            "hfq_adjustment": "PASS", "trading_status": "PASS",
        },
        "pit_fundamentals_used": False,
        "training_dataset_unlocked": False,
        "scope": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
        "source_revisions": revisions,
        "source_receipts": [{
            "dataset": "daily_bars",
            "receipt": {"replayable": True, "receipt_id": "adapter-r1", "dependencies": dependencies},
        }],
        "receipt_provenance": {"dependency_identities": dependencies},
        "acceptance_source_receipts": {
            "historical_universe": {"replayable": True, "receipt_id": "universe-r1"},
            "hfq": {"replayable": True, "receipt_id": "hfq-r1"},
        },
        "limit_evidence": {
            "revision": "limits-r1",
            "source_revision_identity": revisions["price_limit_rules"],
        },
    }
    manifest = {
        "experiment_id": "fill-stress-smoke",
        "data_provenance": provenance,
        "training_dataset_unlocked": False,
        "model_training_performed": False,
    }
    folds = [{
        "fold": {
            "fold_id": "fold_001", "test_start": "2020-03-01",
            "test_end": "2020-06-30", "test_sessions": 2,
        },
    }]
    if orders is None:
        orders = pl.DataFrame({
            "requested_qty": [100, 100, 100, None],
            "filled_qty": [100, 50, 0, 0],
            "status": ["filled", "partially_filled", "no_fill", "no_fill"],
            "reason": [None, None, "suspended", "missing_bar"],
        })
    return VerifiedWalkForwardExperiment(
        path=Path(tmp_path) / "fill-stress-smoke",
        manifest=manifest, folds=folds, fold_table=pl.DataFrame(),
        oos_daily=pl.DataFrame(), oos_orders=orders, oos_holdings=pl.DataFrame(),
    )

def test_fill_stress_reports_observed_baseline_and_marks_target_ratios_unsupported(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path)
    monkeypatch.setattr(fill_stress, "verify_walk_forward_experiment", lambda _path: experiment)

    report = fill_stress.build_fill_stress_assessment(str(experiment.path))

    assert report["observed_baseline_execution"]["share_fill_ratio"] == pytest.approx(0.5)
    assert report["observed_baseline_execution"]["no_fill_reasons"] == {
        "missing_bar": 1, "suspended": 1,
    }
    assert [item["target_fill_ratio"] for item in report["scenarios"]] == [1.0, 0.95, 0.9, 0.8, 0.7]
    assert {item["status"] for item in report["scenarios"]} == {"UNSUPPORTED"}
    assert all(item["achieved_fill_ratio"] is None for item in report["scenarios"])
    assert report["engine_capabilities"]["deterministic_target_fill_ratio_policy"] is False
    assert report["training_dataset_unlocked"] is False


def test_fill_stress_blocks_scenarios_when_baseline_admission_fails(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, fail_gate=True)
    monkeypatch.setattr(fill_stress, "verify_walk_forward_experiment", lambda _path: experiment)

    report = fill_stress.build_fill_stress_assessment(str(experiment.path))

    assert report["admission_decision"] == "BLOCKED"
    assert {item["status"] for item in report["scenarios"]} == {"BLOCKED"}


def test_fill_stress_rejects_inconsistent_order_quantities(tmp_path, monkeypatch):
    orders = pl.DataFrame({
        "requested_qty": [100], "filled_qty": [101],
        "status": ["filled"], "reason": [None],
    })
    experiment = make_experiment(tmp_path, orders=orders)
    monkeypatch.setattr(fill_stress, "verify_walk_forward_experiment", lambda _path: experiment)

    with pytest.raises(ValueError, match="cannot exceed requested_qty"):
        fill_stress.build_fill_stress_assessment(str(experiment.path))



def test_fill_stress_uses_target_transition_denominator_for_retries(tmp_path, monkeypatch):
    orders = pl.DataFrame({
        "target_signal_date": ["2020-03-01", "2020-03-01"],
        "target_requested_qty": [100, 100],
        "instrument": ["600000.SH", "600000.SH"],
        "requested_qty": [100, 50],
        "filled_qty": [50, 50],
        "status": ["partially_filled", "filled"],
        "reason": [None, None],
    })
    experiment = make_experiment(tmp_path, orders=orders)
    monkeypatch.setattr(fill_stress, "verify_walk_forward_experiment", lambda _path: experiment)

    report = fill_stress.build_fill_stress_assessment(str(experiment.path))

    observed = report["observed_baseline_execution"]
    assert observed["share_fill_ratio"] == pytest.approx(1.0)
    assert observed["share_fill_ratio_basis"] == "target_transition"
    assert observed["requested_shares"] == 100
    assert observed["filled_shares"] == 100
    assert report["engine_capabilities"]["target_transition_ledger"] is True


def test_observed_execution_validates_target_caps_and_counts_retry_fills_once():
    orders = pl.DataFrame({
        "target_signal_date": ["2020-03-01", "2020-03-01"],
        "target_requested_qty": [100, 100],
        "target_fill_cap_qty": [50, 50],
        "target_fill_ratio": [0.5, 0.5],
        "instrument": ["600000.SH", "600000.SH"],
        "requested_qty": [50, 25],
        "filled_qty": [25, 25],
        "status": ["partially_filled", "filled"],
        "reason": [None, None],
    })
    observed = fill_stress._observed_execution(orders)
    assert observed["requested_shares"] == 100
    assert observed["target_fill_cap_shares"] == 50
    assert observed["target_fill_cap_ratio"] == pytest.approx(0.5)
    assert observed["filled_shares"] == 50
    assert observed["share_fill_ratio"] == pytest.approx(0.5)
    assert observed["share_fill_ratio_basis"] == "target_transition"


def test_observed_execution_rejects_changed_target_fill_cap_across_retries():
    orders = pl.DataFrame({
        "target_signal_date": ["2020-03-01", "2020-03-01"],
        "target_requested_qty": [100, 100],
        "target_fill_cap_qty": [50, 60],
        "target_fill_ratio": [0.5, 0.5],
        "instrument": ["600000.SH", "600000.SH"],
        "requested_qty": [50, 50],
        "filled_qty": [0, 0],
        "status": ["no_fill", "no_fill"],
        "reason": ["suspended", "suspended"],
    })
    with pytest.raises(ValueError, match="Target fill cap changes"):
        fill_stress._observed_execution(orders)


def test_observed_execution_rejects_cumulative_fill_above_cap():
    orders = pl.DataFrame({
        "target_signal_date": ["2020-03-01", "2020-03-01"],
        "target_requested_qty": [100, 100],
        "target_fill_cap_qty": [50, 50],
        "target_fill_ratio": [0.5, 0.5],
        "instrument": ["600000.SH", "600000.SH"],
        "requested_qty": [50, 25],
        "filled_qty": [30, 25],
        "status": ["partially_filled", "filled"],
        "reason": [None, None],
    })
    with pytest.raises(ValueError, match="exceed target fill cap"):
        fill_stress._observed_execution(orders)


def test_run_fill_stress_suite_persists_and_verifies_f0_to_f4(tmp_path, monkeypatch):
    from dataclasses import asdict
    from ashare_quant.backtest import BacktestConfig
    from ashare_quant.research.walk_forward import WalkForwardConfig
    from ashare_quant.strategies.momentum import MomentumConfig

    walk_config = WalkForwardConfig(
        min_train_sessions=2, validation_sessions=1, test_sessions=1,
        label_horizon_sessions=0, embargo_sessions=0,
    )
    strategy_config = MomentumConfig()
    baseline_config = BacktestConfig()
    configs = {
        "walk_forward": asdict(walk_config),
        "strategy": asdict(strategy_config),
        "backtest": asdict(baseline_config),
    }
    provenance = {"acceptance_report_id": "acceptance-001"}
    fold = {
        "fold": {
            "fold_id": "fold_001", "train_start": "2020-01-01",
            "train_end": "2020-01-02", "validation_start": "2020-01-03",
            "validation_end": "2020-01-03", "test_start": "2020-01-04",
            "test_end": "2020-01-04", "test_sessions": 1,
        }
    }
    created = {"baseline": None}
    scenario_ratios = {}

    def make_verified(path, ratio, experiment_id):
        cap = int(1000 * ratio // 100 * 100)
        orders = pl.DataFrame({
            "target_signal_date": ["2020-01-03"],
            "target_requested_qty": [1000],
            "target_fill_cap_qty": [cap],
            "target_fill_ratio": [ratio],
            "instrument": ["600000.SH"],
            "requested_qty": [cap],
            "filled_qty": [cap],
            "status": ["filled" if cap else "no_fill"],
            "reason": [None if cap else "target_fill_cap_below_board_lot"],
        })
        manifest = {
            "experiment_id": experiment_id,
            "experiment_type": "walk_forward_momentum",
            "training_dataset_unlocked": False,
            "model_training_performed": False,
            "configs": {
                **configs,
                "backtest": {**configs["backtest"], "target_fill_ratio": ratio},
            },
            "data_provenance": provenance,
            "aggregate_report": {
                "test_windows_contiguous": True,
                "fold_linked_total_return": 0.1,
                "fold_linked_cagr": 0.08,
                "worst_fold_max_drawdown": -0.2,
            },
            "config_sha256": "a" * 64,
        }
        return VerifiedWalkForwardExperiment(
            path=Path(path), manifest=manifest, folds=[fold],
            fold_table=pl.DataFrame(), oos_daily=pl.DataFrame(),
            oos_orders=orders, oos_holdings=pl.DataFrame(),
        )

    baseline = make_verified("baseline", 1.0, "baseline-001")
    monkeypatch.setattr(fill_stress, "verify_walk_forward_experiment", lambda path: (
        baseline if str(path) == "baseline" else make_verified(
            path, scenario_ratios[str(path)], Path(path).name,
        )
    ))
    monkeypatch.setattr(
        fill_stress, "build_research_qa_report",
        lambda *args, **kwargs: {"admission": {"decision": "PRICE_ONLY_ELIGIBLE"}},
    )
    monkeypatch.setattr(fill_stress, "run_walk_forward_momentum", lambda *args, **kwargs: object())

    def persist(_run, *, root, experiment_id, backtest_config, **kwargs):
        scenario_ratios[str(Path(root) / experiment_id)] = backtest_config.target_fill_ratio
        return Path(root) / experiment_id

    monkeypatch.setattr(fill_stress, "persist_walk_forward_momentum_run", persist)

    report = fill_stress.run_fill_stress_suite(
        object(), baseline_experiment_path="baseline",
        experiment_root=tmp_path, experiment_id_prefix="fill-suite",
        repository_commit="abcdef1234567",
    )
    assert report["protocol_version"] == "phase10.5"
    assert [item["scenario_id"] for item in report["scenarios"]] == ["F0", "F1", "F2", "F3", "F4"]
    assert [item["target_fill_ratio"] for item in report["scenarios"]] == [1.0, 0.95, 0.9, 0.8, 0.7]
    assert [item["achieved_fill_ratio"] for item in report["scenarios"]] == [1.0, 0.9, 0.9, 0.8, 0.7]
    assert report["integrity"]["all_artifacts_verified"] is True
    assert report["training_dataset_unlocked"] is False
