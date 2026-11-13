from pathlib import Path

import polars as pl
import pytest

from ashare_quant.research import (
    VerifiedWalkForwardExperiment,
    build_research_qa_report,
    validate_research_qa_report,
)


def make_verified_experiment(tmp_path, *, gates=None, pit_used=False, scope=None):
    gate_statuses = gates or {
        "historical_universe": "PASS",
        "hfq_adjustment": "PASS",
        "point_in_time_fundamentals": "FAIL",
        "trading_status": "PASS",
    }
    window = {"start": "2020-01-01", "end": "2020-12-31"}
    provenance = {
        "acceptance_report_id": "acceptance-001",
        "acceptance_report_window": window,
        "universe_profile": "cn_a_sh_sz_research_v1",
        "acceptance_gates": gate_statuses,
        "required_benchmark_gates": {
            key: gate_statuses[key]
            for key in ("historical_universe", "hfq_adjustment", "trading_status")
        },
        "pit_fundamentals_used": pit_used,
        "training_dataset_unlocked": False,
        "source_revisions": {
            "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
            "universe": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
            "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
            "price_limit_rules": {"revision_id": "limits-r1", "contract_fingerprint": "limits-contract", "source": "synthetic-provider"},
        },
        "source_receipts": [
            {"dataset": "daily_bars", "receipt": {"replayable": True, "receipt_id": "adapter-1", "dependencies": {
                "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
                "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
                "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
                "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
            }}},
        ],
        "receipt_provenance": {"dependency_identities": {
            "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
            "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
            "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
            "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
        }},
        "acceptance_source_receipts": {
            "historical_universe": {"replayable": True, "revision": "universe-r1"},
            "hfq": {"replayable": True, "revision": "hfq-r1"},
        },
        "limit_evidence": {"revision": "limits-r1", "source_revision_identity": {
            "revision_id": "limits-r1", "contract_fingerprint": "limits-contract", "source": "synthetic-provider",
        }},
        "scope": scope or {"start_date": window["start"], "end_date": window["end"]},
    }
    manifest = {
        "experiment_id": "momentum-qa-001",
        "experiment_type": "walk_forward_momentum",
        "repository_commit": "abcdef1234567",
        "training_dataset_unlocked": False,
        "model_training_performed": False,
        "data_provenance": provenance,
        "artifacts": {
            "oos_daily": {"sha256": "a" * 64},
            "oos_orders": {"sha256": "b" * 64},
        },
    }
    folds = [
        {
            "fold": {
                "fold_id": "fold_001",
                "test_start": "2020-03-01",
                "test_end": "2020-06-30",
            }
        }
    ]
    return VerifiedWalkForwardExperiment(
        path=Path(tmp_path) / "momentum-qa-001",
        manifest=manifest,
        folds=folds,
        fold_table=pl.DataFrame(),
        oos_daily=pl.DataFrame(),
        oos_orders=pl.DataFrame(),
        oos_holdings=pl.DataFrame(),
    )


def build_report(experiment, monkeypatch):
    from ashare_quant.research import qa as qa_module

    monkeypatch.setattr(
        qa_module, "verify_walk_forward_experiment", lambda _path: experiment
    )
    return build_research_qa_report(
        str(experiment.path),
        qa_id="qa-smoke-001",
        created_at="2026-11-13T00:00:00Z",
        repository_commit="abcdef1234567",
    )


def test_price_only_gate_can_pass_while_pit_fundamentals_fails(tmp_path, monkeypatch):
    report = build_report(make_verified_experiment(tmp_path), monkeypatch)

    assert report["admission"]["decision"] == "PRICE_ONLY_ELIGIBLE"
    assert report["admission"]["model_training_decision"] == "MODEL_TRAINING_BLOCKED"
    assert report["admission"]["gates"] == {
        "historical_universe": "PASS",
        "hfq": "PASS",
        "pit_fundamentals": "FAIL",
        "trading_status": "PASS",
    }
    assert report["admission"]["training_dataset_unlocked"] is False
    assert report["conclusion"] == "INSUFFICIENT_EVIDENCE"
    assert "PIT fundamentals did not PASS" in report["limitations"][0]


@pytest.mark.parametrize("gate", ["historical_universe", "hfq_adjustment", "trading_status"])
def test_any_required_price_only_gate_failure_blocks_qa(tmp_path, monkeypatch, gate):
    statuses = {
        "historical_universe": "PASS",
        "hfq_adjustment": "PASS",
        "point_in_time_fundamentals": "PASS",
        "trading_status": "PASS",
    }
    statuses[gate] = "FAIL"
    report = build_report(make_verified_experiment(tmp_path, gates=statuses), monkeypatch)

    assert report["admission"]["decision"] == "BLOCKED"
    assert report["admission"]["model_training_decision"] == "MODEL_TRAINING_BLOCKED"
    assert report["conclusion"] == "BLOCKED"


def test_model_training_is_only_gate_eligible_when_all_four_pass(tmp_path, monkeypatch):
    statuses = {
        "historical_universe": "PASS",
        "hfq_adjustment": "PASS",
        "point_in_time_fundamentals": "PASS",
        "trading_status": "PASS",
    }
    report = build_report(make_verified_experiment(tmp_path, gates=statuses), monkeypatch)

    assert report["admission"]["decision"] == "PRICE_ONLY_ELIGIBLE"
    assert report["admission"]["model_training_decision"] == "MODEL_TRAINING_ELIGIBLE"
    assert report["admission"]["training_dataset_unlocked"] is False
    assert report["admission"]["model_training_performed"] is False


def test_missing_fourth_gate_fails_closed(tmp_path, monkeypatch):
    statuses = {
        "historical_universe": "PASS",
        "hfq_adjustment": "PASS",
        "trading_status": "PASS",
    }
    report = build_report(make_verified_experiment(tmp_path, gates=statuses), monkeypatch)

    assert report["admission"]["decision"] == "BLOCKED"
    assert report["admission"]["gates"]["pit_fundamentals"] == "MISSING"
    assert any("four-gate" in item for item in report["admission"]["blockers"])


def test_acceptance_window_must_match_bundle_scope(tmp_path, monkeypatch):
    report = build_report(
        make_verified_experiment(
            tmp_path,
            scope={"start_date": "2019-01-01", "end_date": "2020-12-31"},
        ),
        monkeypatch,
    )
    assert report["admission"]["decision"] == "BLOCKED"
    assert any("scope" in item for item in report["admission"]["blockers"])


def test_receipt_revision_mismatch_blocks_qa_admission(tmp_path, monkeypatch):
    experiment = make_verified_experiment(tmp_path)
    provenance = {
        **experiment.manifest["data_provenance"],
        "source_revisions": {
            **experiment.manifest["data_provenance"]["source_revisions"],
            "daily_bars": {
                "revision_id": "caller-invented-r9",
                "contract_fingerprint": "daily-contract",
            },
        },
    }
    invalid = VerifiedWalkForwardExperiment(
        path=experiment.path,
        manifest={**experiment.manifest, "data_provenance": provenance},
        folds=experiment.folds,
        fold_table=experiment.fold_table,
        oos_daily=experiment.oos_daily,
        oos_orders=experiment.oos_orders,
        oos_holdings=experiment.oos_holdings,
    )
    report = build_report(invalid, monkeypatch)

    assert report["admission"]["decision"] == "BLOCKED"
    assert any(
        "does not match retained receipt identity" in blocker
        for blocker in report["admission"]["blockers"]
    )


def test_report_schema_rejects_training_unlock_and_missing_gate(tmp_path, monkeypatch):
    report = build_report(make_verified_experiment(tmp_path), monkeypatch)
    invalid = {**report, "admission": {**report["admission"], "training_dataset_unlocked": True}}
    with pytest.raises(ValueError, match="never unlock"):
        validate_research_qa_report(invalid)

    invalid_gates = {
        **report,
        "admission": {
            **report["admission"],
            "gates": {"historical_universe": "PASS"},
        },
    }
    with pytest.raises(ValueError, match="exactly the four"):
        validate_research_qa_report(invalid_gates)


def test_report_schema_rejects_non_finite_values_and_invalid_conclusion(tmp_path, monkeypatch):
    report = build_report(make_verified_experiment(tmp_path), monkeypatch)
    invalid = {**report, "scenarios": [{"net_return": float("nan")}]}
    with pytest.raises(ValueError, match="JSON compliant"):
        validate_research_qa_report(invalid)

    invalid = {**report, "conclusion": "PROFITABLE"}
    with pytest.raises(ValueError, match="conclusion"):
        validate_research_qa_report(invalid)


def test_unverified_manifest_is_not_accepted(monkeypatch):
    from ashare_quant.research import qa as qa_module

    monkeypatch.setattr(qa_module, "verify_walk_forward_experiment", lambda _path: {"manifest": {}})
    with pytest.raises(TypeError, match="verify_walk_forward_experiment"):
        build_research_qa_report(
            "not-an-experiment",
            qa_id="qa-invalid",
            created_at="2026-11-13T00:00:00Z",
            repository_commit="abcdef1234567",
        )



def test_report_schema_rejects_inconsistent_model_training_gate_decision(tmp_path, monkeypatch):
    report = build_report(make_verified_experiment(tmp_path), monkeypatch)
    invalid = {
        **report,
        "admission": {
            **report["admission"],
            "model_training_decision": "MODEL_TRAINING_ELIGIBLE",
        },
    }
    with pytest.raises(ValueError, match="all four gates PASS"):
        validate_research_qa_report(invalid)
