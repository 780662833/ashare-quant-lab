"""Fail-closed data admission and report contracts for Research QA.

This module consumes only experiments that have already passed the Phase 8
artifact verifier. It never reads source data, runs a backtest, or trains a
model.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment

GATE_KEYS = (
    "historical_universe",
    "hfq_adjustment",
    "point_in_time_fundamentals",
    "trading_status",
)
REPORT_GATE_KEYS = (
    "historical_universe",
    "hfq",
    "pit_fundamentals",
    "trading_status",
)
REQUIRED_BENCHMARK_GATES = (
    "historical_universe",
    "hfq_adjustment",
    "trading_status",
)
REQUIRED_SOURCE_REVISIONS = (
    "daily_bars",
    "adj_factors",
    "universe",
    "trading_status",
    "trading_calendar",
    "price_limit_rules",
)
_ALLOWED_GATE_STATUSES = {"PASS", "FAIL", "WARN", "MISSING", "UNKNOWN"}
_ALLOWED_DECISIONS = {
    "BLOCKED",
    "PRICE_ONLY_ELIGIBLE",
    "MODEL_TRAINING_BLOCKED",
    "MODEL_TRAINING_ELIGIBLE",
}
_ALLOWED_CONCLUSIONS = {
    "BLOCKED",
    "INSUFFICIENT_EVIDENCE",
    "FRAGILE",
    "ROBUSTNESS_EVIDENCE_ONLY",
}


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", value):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    except ValueError:
        return False


def _normalize_gate_status(value: Any) -> str:
    status = str(value).upper() if value is not None else "MISSING"
    return status if status in _ALLOWED_GATE_STATUSES else "UNKNOWN"


def _assess_verified_experiment(
    experiment: VerifiedWalkForwardExperiment,
) -> dict[str, Any]:
    if not isinstance(experiment, VerifiedWalkForwardExperiment):
        raise TypeError(
            "experiment must come from verify_walk_forward_experiment(); "
            "unverified manifests are not accepted"
        )

    manifest = experiment.manifest
    provenance = manifest.get("data_provenance")
    blockers: list[str] = []
    if not isinstance(provenance, Mapping):
        provenance = {}
        blockers.append("missing data_provenance")

    raw_gates = provenance.get("acceptance_gates")
    if not isinstance(raw_gates, Mapping):
        raw_gates = {}
        blockers.append("missing four-gate acceptance_gates provenance")
    gates = {
        key: _normalize_gate_status(raw_gates.get(key))
        for key in GATE_KEYS
    }

    required_benchmark = provenance.get("required_benchmark_gates")
    if not isinstance(required_benchmark, Mapping):
        blockers.append("missing required_benchmark_gates provenance")
    else:
        for key in REQUIRED_BENCHMARK_GATES:
            if _normalize_gate_status(required_benchmark.get(key)) != gates[key]:
                blockers.append(f"{key} does not match independent acceptance gate")

    report_window = provenance.get("acceptance_report_window")
    scope = provenance.get("scope")
    if not isinstance(report_window, Mapping) or not isinstance(scope, Mapping):
        blockers.append("missing acceptance report window or bundle scope")
        report_window = {}
        scope = {}
    start = report_window.get("start")
    end = report_window.get("end")
    if not _valid_iso_date(start) or not _valid_iso_date(end) or start > end:
        blockers.append("invalid acceptance report window")
    if scope.get("start_date") != start or scope.get("end_date") != end:
        blockers.append("acceptance report window does not match canonical bundle scope")

    acceptance_report_id = provenance.get("acceptance_report_id")
    universe_profile = provenance.get("universe_profile")
    if not isinstance(acceptance_report_id, str) or not acceptance_report_id.strip():
        blockers.append("missing acceptance_report_id")
    if not isinstance(universe_profile, str) or not universe_profile.strip():
        blockers.append("missing universe_profile")

    revisions = provenance.get("source_revisions")
    if not isinstance(revisions, Mapping):
        revisions = {}
        blockers.append("missing source_revisions")
    missing_revisions = [
        name for name in REQUIRED_SOURCE_REVISIONS
        if not isinstance(revisions.get(name), str) or not revisions.get(name, "").strip()
    ]
    if missing_revisions:
        blockers.append("missing source revisions: " + ", ".join(sorted(missing_revisions)))

    receipts = provenance.get("source_receipts")
    if not isinstance(receipts, Mapping):
        receipts = {}
    for receipt_name in ("historical_universe", "hfq"):
        receipt = receipts.get(receipt_name)
        if not isinstance(receipt, Mapping) or receipt.get("replayable") is not True:
            blockers.append(f"missing replayable {receipt_name} receipt")

    if manifest.get("training_dataset_unlocked") is not False:
        blockers.append("manifest training_dataset_unlocked is not false")
    if manifest.get("model_training_performed") is not False:
        blockers.append("manifest model_training_performed is not false")
    if provenance.get("training_dataset_unlocked") is not False:
        blockers.append("provenance training_dataset_unlocked is not false")

    folds = experiment.folds
    if not folds:
        blockers.append("verified experiment contains no folds")
    for item in folds:
        fold = item.get("fold", {}) if isinstance(item, Mapping) else {}
        fold_start, fold_end = fold.get("test_start"), fold.get("test_end")
        if (
            not _valid_iso_date(fold_start)
            or not _valid_iso_date(fold_end)
            or fold_start > fold_end
            or not _valid_iso_date(start)
            or not _valid_iso_date(end)
            or fold_start < start
            or fold_end > end
        ):
            blockers.append("fold test window falls outside acceptance report window")
            break

    price_only_pass = all(gates[key] == "PASS" for key in REQUIRED_BENCHMARK_GATES)
    pit_used = provenance.get("pit_fundamentals_used")
    if pit_used is not False:
        blockers.append("price-only benchmark must explicitly declare pit_fundamentals_used=false")

    if blockers or not price_only_pass:
        decision = "BLOCKED"
    else:
        decision = "PRICE_ONLY_ELIGIBLE"

    model_training_decision = (
        "MODEL_TRAINING_ELIGIBLE"
        if all(gates[key] == "PASS" for key in GATE_KEYS) and not blockers
        else "MODEL_TRAINING_BLOCKED"
    )
    # This is an admission assessment only; this module never unlocks training.
    return {
        "decision": decision,
        "model_training_decision": model_training_decision,
        "gates": {
            "historical_universe": gates["historical_universe"],
            "hfq": gates["hfq_adjustment"],
            "pit_fundamentals": gates["point_in_time_fundamentals"],
            "trading_status": gates["trading_status"],
        },
        "acceptance_report_id": acceptance_report_id,
        "acceptance_report_window": {"start": start, "end": end},
        "universe_profile": universe_profile,
        "source_revisions": dict(revisions),
        "source_receipts": dict(receipts),
        "training_dataset_unlocked": False,
        "model_training_performed": False,
        "blockers": sorted(set(blockers)),
    }


def validate_research_qa_report(report: Mapping[str, Any]) -> None:
    """Validate the stable Phase 10 QA report envelope; raise ValueError on drift."""
    if not isinstance(report, Mapping):
        raise ValueError("Research QA report must be a mapping")
    required = {
        "schema_version", "protocol_version", "qa_id", "created_at",
        "repository_commit", "experiment_ids", "admission", "integrity",
        "scenarios", "periods", "regimes", "limitations", "conclusion",
    }
    missing = sorted(required.difference(report))
    if missing:
        raise ValueError("Research QA report is missing fields: " + ", ".join(missing))
    if report.get("schema_version") != 1 or report.get("protocol_version") != "phase10.2":
        raise ValueError("Unsupported Research QA report schema/protocol version")
    if not isinstance(report.get("qa_id"), str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", report["qa_id"]
    ):
        raise ValueError("qa_id must be a safe 1-80 character identifier")
    created_at = report.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("created_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be a valid ISO-8601 timestamp") from exc
    commit = report.get("repository_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise ValueError("repository_commit must be a 7-40 character git SHA")
    experiment_ids = report.get("experiment_ids")
    if not isinstance(experiment_ids, list) or not experiment_ids or any(
        not isinstance(item, str) or not item for item in experiment_ids
    ):
        raise ValueError("experiment_ids must be a non-empty list of strings")

    admission = report.get("admission")
    if not isinstance(admission, Mapping):
        raise ValueError("admission must be an object")
    if admission.get("decision") not in _ALLOWED_DECISIONS:
        raise ValueError("admission.decision is invalid")
    if admission.get("model_training_decision") not in {
        "MODEL_TRAINING_BLOCKED", "MODEL_TRAINING_ELIGIBLE"
    }:
        raise ValueError("admission.model_training_decision is invalid")
    gates = admission.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(REPORT_GATE_KEYS):
        raise ValueError("admission.gates must contain exactly the four independent gates")
    if any(_normalize_gate_status(gates[key]) not in _ALLOWED_GATE_STATUSES for key in REPORT_GATE_KEYS):
        raise ValueError("admission.gates contains an invalid status")
    if admission.get("training_dataset_unlocked") is not False:
        raise ValueError("Research QA must never unlock model training")
    if admission.get("model_training_performed") is not False:
        raise ValueError("Research QA must never perform model training")

    integrity = report.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("verified") is not True:
        raise ValueError("integrity.verified must be true for a verified-artifact QA report")
    for field in ("scenarios", "periods", "regimes", "limitations"):
        if not isinstance(report.get(field), list):
            raise ValueError(f"{field} must be a list")
    if report.get("conclusion") not in _ALLOWED_CONCLUSIONS:
        raise ValueError("conclusion is invalid")
    if admission.get("decision") == "BLOCKED" and report.get("conclusion") != "BLOCKED":
        raise ValueError("blocked admission requires BLOCKED conclusion")
    # Enforce strict JSON serializability and reject NaN/Infinity.
    json.dumps(report, ensure_ascii=False, allow_nan=False)


def build_research_qa_report(
    experiment: VerifiedWalkForwardExperiment,
    *,
    qa_id: str,
    created_at: str,
    repository_commit: str,
) -> dict[str, Any]:
    """Build a P10.2 report skeleton from a Phase 8 verified artifact.

    Callers must pass the object returned by verify_walk_forward_experiment().
    The report deliberately contains no performance verdict: later P10 phases
    populate scenarios, periods and regimes from verified ledgers.
    """
    admission = _assess_verified_experiment(experiment)
    manifest_bytes = json.dumps(
        experiment.manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    artifacts = experiment.manifest.get("artifacts", {})
    report = {
        "schema_version": 1,
        "protocol_version": "phase10.2",
        "qa_id": qa_id,
        "created_at": created_at,
        "repository_commit": repository_commit.lower(),
        "experiment_ids": [str(experiment.manifest.get("experiment_id", ""))],
        "admission": admission,
        "integrity": {
            "verified": True,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "artifact_sha256": {
                name: record.get("sha256")
                for name, record in artifacts.items()
                if isinstance(record, Mapping)
            },
        },
        "scenarios": [],
        "periods": [],
        "regimes": [],
        "limitations": [],
        "conclusion": (
            "BLOCKED" if admission["decision"] == "BLOCKED"
            else "INSUFFICIENT_EVIDENCE"
        ),
    }
    if admission["gates"]["pit_fundamentals"] != "PASS":
        report["limitations"].append(
            "PIT fundamentals did not PASS; this price-only benchmark does not consume PIT, "
            "but LightGBM training remains blocked."
        )
    if admission["blockers"]:
        report["limitations"].extend(admission["blockers"])
    validate_research_qa_report(report)
    return report
