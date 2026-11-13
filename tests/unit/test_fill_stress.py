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
