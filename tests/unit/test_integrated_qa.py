import hashlib
import json

import pytest

from ashare_quant.research import fill_stress, statistical_qa
from ashare_quant.research import integrated_qa

def base_report():
    return {
        "schema_version": 1, "protocol_version": "phase10.2",
        "qa_id": "qa-integrated-001", "created_at": "2026-11-13T10:00:00+00:00",
        "repository_commit": "abcdef1234567", "experiment_ids": ["experiment-001"],
        "admission": {
            "decision": "PRICE_ONLY_ELIGIBLE",
            "model_training_decision": "MODEL_TRAINING_BLOCKED",
            "gates": {
                "historical_universe": "PASS", "hfq": "PASS",
                "pit_fundamentals": "FAIL", "trading_status": "PASS",
            },
            "training_dataset_unlocked": False, "model_training_performed": False,
            "blockers": [],
        },
        "integrity": {"verified": True, "manifest_sha256": "a" * 64, "artifact_sha256": {}},
        "scenarios": [], "periods": [], "regimes": [],
        "limitations": ["PIT fundamentals remain blocked for model training."],
        "conclusion": "INSUFFICIENT_EVIDENCE",
    }

def fill_report(admission="PRICE_ONLY_ELIGIBLE"):
    return {
        "experiment_id": "experiment-001",
        "admission_decision": admission,
        "observed_baseline_execution": {"share_fill_ratio": 0.91, "no_fill_reasons": {"suspended": 2}},
        "engine_capabilities": {"deterministic_target_fill_ratio_policy": False},
        "scenarios": [
            {"scenario_id": f"F{i}", "target_fill_ratio": ratio,
             "status": "BLOCKED" if admission == "BLOCKED" else "UNSUPPORTED",
             "achieved_fill_ratio": None, "reason": "no deterministic fill policy"}
            for i, ratio in enumerate((1.0, 0.95, 0.9, 0.8, 0.7))
        ],
        "limitations": ["Target fill-ratio scenarios are unsupported."],
        "training_dataset_unlocked": False,
    }

def stats_report(admission="PRICE_ONLY_ELIGIBLE"):
    return {
        "experiment_id": "experiment-001",
        "admission_decision": admission,
        "decision": "BLOCKED" if admission == "BLOCKED" else "DESCRIPTIVE_ONLY",
        "positive_fold_ratio": 0.6,
        "limitations": ["No dependence-aware inference."],
    }

def build(monkeypatch, *, cost=None, fill=None, admission=None):
    base = base_report()
    if admission is not None:
        base["admission"]["decision"] = admission
        if admission == "BLOCKED":
            base["admission"]["gates"]["historical_universe"] = "FAIL"
            base["conclusion"] = "BLOCKED"
    monkeypatch.setattr(integrated_qa, "build_research_qa_report", lambda *args, **kwargs: base)
    monkeypatch.setattr(fill_stress, "build_fill_stress_assessment", lambda _path: fill_report(admission or "PRICE_ONLY_ELIGIBLE"))
    monkeypatch.setattr(statistical_qa, "build_statistical_qa_summary", lambda _path: stats_report(admission or "PRICE_ONLY_ELIGIBLE"))
    return integrated_qa.build_integrated_research_qa_report(
        "verified-experiment", qa_id="qa-integrated-001",
        created_at="2026-11-13T10:00:00+00:00",
        repository_commit="abcdef1234567", cost_stress_report=cost,
        fill_stress_report=fill,
    )

def cost_report():
    report = {
        "protocol_version": "phase10.4",
        "repository_commit": "abcdef1234567",
        "baseline_experiment_id": "experiment-001",
        "experiment_ids": ["experiment-001", "experiment-001-C1", "experiment-001-C2", "experiment-001-C3"],
        "training_dataset_unlocked": False, "model_training_performed": False,
        "integrity": {
            "all_artifacts_verified": True,
            "admission_decisions": ["PRICE_ONLY_ELIGIBLE"] * 4,
            "config_sha256": ["b" * 64] * 4,
            "data_provenance_sha256": "c" * 64,
        },
        "scenarios": [
            {"scenario_id": f"C{i}", "experiment_id": experiment_id,
             "cost_multiplier": multiplier,
             "net_return": 0.1 / multiplier, "cagr": 0.08 / multiplier}
            for i, (multiplier, experiment_id) in enumerate(zip(
                (1.0, 1.5, 2.0, 3.0),
                ("experiment-001", "experiment-001-C1", "experiment-001-C2", "experiment-001-C3"),
            ))
        ],
        "limitations": ["Cost multipliers are assumptions."],
    }
    report["integrity"]["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return report

def test_integrated_report_includes_sections_and_fails_closed_without_cost_report(monkeypatch):
    report = build(monkeypatch)
    assert report["protocol_version"] == "phase10.7"
    assert report["cost_stress"]["status"] == "NOT_PROVIDED"
    assert len(report["execution_stress"]["scenarios"]) == 5
    assert report["statistical_qa"]["decision"] == "DESCRIPTIVE_ONLY"
    assert len(report["scenarios"]) == 5
    assert report["conclusion"] == "INSUFFICIENT_EVIDENCE"
    assert "No verified P10.4 cost-stress report was supplied." in report["limitations"]


def test_integrated_report_accepts_only_matching_verified_cost_report(monkeypatch):
    report = build(monkeypatch, cost=cost_report())
    assert report["cost_stress"]["status"] == "VERIFIED"
    assert len(report["cost_stress"]["scenarios"]) == 4
    assert report["integrity"]["cost_stress_sha256"] == cost_report()["integrity"]["report_sha256"]


def test_integrated_report_rejects_tampered_cost_report_hash(monkeypatch):
    cost = cost_report()
    cost["scenarios"][0]["net_return"] = 0.99
    with pytest.raises(ValueError, match="content does not match its SHA-256"):
        build(monkeypatch, cost=cost)

def test_integrated_report_rejects_cost_report_for_another_baseline(monkeypatch):
    cost = cost_report()
    cost["baseline_experiment_id"] = "other-experiment"
    with pytest.raises(ValueError, match="baseline does not match"):
        build(monkeypatch, cost=cost)


def test_integrated_report_keeps_blocked_admission_blocked(monkeypatch):
    report = build(monkeypatch, admission="BLOCKED")
    assert report["admission"]["decision"] == "BLOCKED"
    assert report["conclusion"] == "BLOCKED"


def test_write_research_qa_report_is_write_once(tmp_path, monkeypatch):
    report = build(monkeypatch)
    written = integrated_qa.write_research_qa_report(report, output_root=tmp_path)
    assert (tmp_path / "qa-integrated-001" / "report.json").exists()
    assert (tmp_path / "qa-integrated-001" / "report.md").exists()
    saved = json.loads((tmp_path / "qa-integrated-001" / "report.json").read_text(encoding="utf-8"))
    assert saved["protocol_version"] == "phase10.7"
    assert "Integrated Research QA Report" in (tmp_path / "qa-integrated-001" / "report.md").read_text(encoding="utf-8")
    assert len(written["json_sha256"]) == 64
    with pytest.raises(FileExistsError):
        integrated_qa.write_research_qa_report(report, output_root=tmp_path)


def fill_suite_report():
    report = {
        "schema_version": 1, "protocol_version": "phase10.5",
        "repository_commit": "abcdef1234567",
        "baseline_experiment_id": "experiment-001",
        "experiment_ids": ["experiment-001", "experiment-001-F1", "experiment-001-F2", "experiment-001-F3", "experiment-001-F4"],
        "scenario_ratios": [1.0, 0.95, 0.90, 0.80, 0.70],
        "scenarios": [
            {"scenario_id": f"F{i}", "experiment_id": experiment_id,
             "target_fill_ratio": ratio, "status": "VERIFIED",
             "achieved_fill_ratio": ratio, "target_fill_cap_ratio": ratio}
            for i, (ratio, experiment_id) in enumerate(zip(
                (1.0, 0.95, 0.90, 0.80, 0.70),
                ("experiment-001", "experiment-001-F1", "experiment-001-F2", "experiment-001-F3", "experiment-001-F4"),
            ))
        ],
        "integrity": {
            "all_artifacts_verified": True,
            "admission_decisions": ["PRICE_ONLY_ELIGIBLE"] * 5,
            "config_sha256": ["b" * 64] * 5,
            "data_provenance_sha256": "c" * 64,
        },
        "limitations": ["Fill caps are not a market liquidity model."],
        "training_dataset_unlocked": False, "model_training_performed": False,
    }
    report["integrity"]["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return report


def test_integrated_report_accepts_verified_fill_stress_suite(monkeypatch):
    report = build(monkeypatch, fill=fill_suite_report())
    assert report["fill_stress"]["status"] == "VERIFIED"
    assert [x["scenario_id"] for x in report["execution_stress"]["scenarios"]] == ["F0", "F1", "F2", "F3", "F4"]
    assert all(x["status"] == "VERIFIED" for x in report["execution_stress"]["scenarios"])
    assert report["integrity"]["fill_stress_suite_sha256"] == fill_suite_report()["integrity"]["report_sha256"]
    assert "No verified F0-F4" not in " ".join(report["limitations"])


def test_integrated_report_rejects_tampered_fill_stress_hash(monkeypatch):
    suite = fill_suite_report()
    suite["scenarios"][1]["achieved_fill_ratio"] = 0.99
    with pytest.raises(ValueError, match="content does not match its SHA-256"):
        build(monkeypatch, fill=suite)


@pytest.mark.parametrize("mutation, message", [
    (lambda x: x.update(baseline_experiment_id="other"), "baseline does not match"),
    (lambda x: x.update(repository_commit="deadbeef"), "repository commit does not match"),
    (lambda x: x.update(training_dataset_unlocked=True), "training lock"),
    (lambda x: x["scenarios"].pop(), "F0-F4 scenarios"),
    (lambda x: x["scenarios"][2].update(status="UNSUPPORTED"), "must all be VERIFIED"),
    (lambda x: x["integrity"].update(admission_decisions=["BLOCKED"] * 5), "admission was not"),
])
def test_integrated_report_rejects_unverified_fill_stress_suite(monkeypatch, mutation, message):
    suite = fill_suite_report()
    mutation(suite)
    # Mutations are intentionally rejected before report-hash verification where applicable.
    with pytest.raises(ValueError, match=message):
        build(monkeypatch, fill=suite)


def test_integrated_report_does_not_accept_fill_suite_for_blocked_baseline(monkeypatch):
    with pytest.raises(ValueError, match="blocked baseline"):
        build(monkeypatch, fill=fill_suite_report(), admission="BLOCKED")



@pytest.mark.parametrize("mutation, message", [
    (lambda x: x["experiment_ids"].__setitem__(1, "experiment-001"), "four unique IDs"),
    (lambda x: x["scenarios"][1].update(experiment_id="wrong-run"), "scenario experiment IDs"),
    (lambda x: x["scenarios"][2].update(cost_multiplier=9.0), "multipliers"),
    (lambda x: x["integrity"].update(config_sha256=["bad"] * 4), "config hashes"),
    (lambda x: x["integrity"].pop("data_provenance_sha256"), "data provenance hash"),
])
def test_integrated_report_rejects_misaligned_cost_stress_artifacts(monkeypatch, mutation, message):
    cost = cost_report()
    mutation(cost)
    with pytest.raises(ValueError, match=message):
        build(monkeypatch, cost=cost)
