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

def build(monkeypatch, *, cost=None, admission=None):
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
    )

def cost_report():
    return {
        "protocol_version": "phase10.4",
        "repository_commit": "abcdef1234567",
        "baseline_experiment_id": "experiment-001",
        "experiment_ids": ["experiment-001", "experiment-001-C1", "experiment-001-C2", "experiment-001-C3"],
        "training_dataset_unlocked": False, "model_training_performed": False,
        "integrity": {
            "all_artifacts_verified": True,
            "admission_decisions": ["PRICE_ONLY_ELIGIBLE"] * 4,
            "report_sha256": "b" * 64,
        },
        "scenarios": [
            {"scenario_id": f"C{i}", "cost_multiplier": multiplier,
             "net_return": 0.1 / multiplier, "cagr": 0.08 / multiplier}
            for i, multiplier in enumerate((1.0, 1.5, 2.0, 3.0))
        ],
        "limitations": ["Cost multipliers are assumptions."],
    }

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
    assert report["integrity"]["cost_stress_sha256"] == "b" * 64


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
