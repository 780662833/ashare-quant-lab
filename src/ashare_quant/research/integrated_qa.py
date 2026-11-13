"""P10.7 integrated, write-once QA report assembly."""
from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

from ashare_quant.research.qa import (
    build_research_qa_report,
    validate_research_qa_report,
)

def _verified_fill_stress_section(
    payload: Mapping[str, Any], *, baseline_experiment_id: str,
    repository_commit: str, admission_decision: str,
) -> dict[str, Any]:
    """Validate and normalize an immutable P10.5 F0-F4 suite report."""
    if payload.get("protocol_version") != "phase10.5":
        raise ValueError("fill_stress_report protocol_version must be phase10.5")
    if admission_decision != "PRICE_ONLY_ELIGIBLE":
        raise ValueError("A blocked baseline cannot be combined with a verified P10.5 fill report")
    if payload.get("baseline_experiment_id") != baseline_experiment_id:
        raise ValueError("P10.5 fill-stress baseline does not match the QA experiment")
    if payload.get("repository_commit") != repository_commit.lower():
        raise ValueError("P10.5 fill-stress repository commit does not match the QA report")
    if payload.get("training_dataset_unlocked") is not False or payload.get("model_training_performed") is not False:
        raise ValueError("P10.5 fill-stress report violates the training lock")
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("all_artifacts_verified") is not True:
        raise ValueError("P10.5 fill-stress artifacts are not all verified")
    decisions = integrity.get("admission_decisions")
    if not isinstance(decisions, list) or decisions != ["PRICE_ONLY_ELIGIBLE"] * 5:
        raise ValueError("P10.5 fill-stress admission was not PRICE_ONLY_ELIGIBLE for every scenario")
    expected_ids = ["F0", "F1", "F2", "F3", "F4"]
    expected_ratios = [1.0, 0.95, 0.90, 0.80, 0.70]
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 5 or not all(isinstance(x, Mapping) for x in scenarios):
        raise ValueError("P10.5 fill-stress report must contain structured F0-F4 scenarios")
    if [x.get("scenario_id") for x in scenarios] != expected_ids:
        raise ValueError("P10.5 fill-stress report must contain F0-F4 in protocol order")
    if any(x.get("status") != "VERIFIED" for x in scenarios):
        raise ValueError("P10.5 fill-stress scenarios must all be VERIFIED")
    if [x.get("target_fill_ratio") for x in scenarios] != expected_ratios:
        raise ValueError("P10.5 fill-stress target ratios do not match the registered protocol")
    for item in scenarios:
        share_fields = (
            "target_requested_shares", "target_fill_cap_shares", "filled_shares",
            "order_count", "filled_order_count", "partially_filled_order_count",
            "no_fill_order_count",
        )
        for field in share_fields:
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} has invalid {field}")
        requested = item["target_requested_shares"]
        cap = item["target_fill_cap_shares"]
        filled = item["filled_shares"]
        if cap > requested or filled > cap:
            raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} exceeds its target/cap shares")
        counts = (
            item["filled_order_count"] + item["partially_filled_order_count"]
            + item["no_fill_order_count"]
        )
        if counts != item["order_count"]:
            raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} order counts do not reconcile")
        reasons = item.get("no_fill_reasons")
        if not isinstance(reasons, Mapping) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in reasons.values()
        ) or sum(reasons.values()) != item["no_fill_order_count"]:
            raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} no-fill reasons do not reconcile")
        cap_ratio = item.get("target_fill_cap_ratio")
        achieved_ratio = item.get("achieved_fill_ratio")
        if requested == 0:
            if cap_ratio is not None or achieved_ratio is not None or cap or filled:
                raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} has ratios without requested shares")
        else:
            expected_cap_ratio = cap / requested
            expected_achieved_ratio = filled / requested
            for field, value, expected in (
                ("target_fill_cap_ratio", cap_ratio, expected_cap_ratio),
                ("achieved_fill_ratio", achieved_ratio, expected_achieved_ratio),
            ):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                    raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} has invalid {field}")
                if abs(float(value) - expected) > 1e-9:
                    raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} {field} does not match share counts")
            if cap_ratio > item["target_fill_ratio"] + 1e-9 or achieved_ratio > cap_ratio + 1e-9:
                raise ValueError(f"P10.5 fill-stress scenario {item['scenario_id']} achieved fills exceed its cap")
    experiment_ids = payload.get("experiment_ids")
    if not isinstance(experiment_ids, list) or len(experiment_ids) != 5 or not experiment_ids or experiment_ids[0] != baseline_experiment_id:
        raise ValueError("P10.5 fill-stress experiment IDs do not include the matching F0 baseline")
    if [x.get("experiment_id") for x in scenarios] != experiment_ids:
        raise ValueError("P10.5 fill-stress scenario experiment IDs do not match the verified artifact list")
    if payload.get("scenario_ratios") != expected_ratios:
        raise ValueError("P10.5 fill-stress scenario ratio registry is invalid")
    for field, expected_length in (("config_sha256", 5),):
        values = integrity.get(field)
        if not isinstance(values, list) or len(values) != expected_length or any(
            not isinstance(value, str) or len(value) != 64 for value in values
        ):
            raise ValueError(f"P10.5 fill-stress {field} values are missing or invalid")
    provenance_hash = integrity.get("data_provenance_sha256")
    if not isinstance(provenance_hash, str) or len(provenance_hash) != 64:
        raise ValueError("P10.5 fill-stress data provenance hash is missing or invalid")
    declared_hash = integrity.get("report_sha256")
    if not isinstance(declared_hash, str) or len(declared_hash) != 64:
        raise ValueError("P10.5 fill-stress report hash is missing or invalid")
    unhashed = dict(payload)
    unhashed_integrity = dict(integrity)
    unhashed_integrity.pop("report_sha256", None)
    unhashed["integrity"] = unhashed_integrity
    actual_hash = hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    if actual_hash != declared_hash:
        raise ValueError("P10.5 fill-stress report content does not match its SHA-256")
    return {
        "status": "VERIFIED", "baseline_experiment_id": baseline_experiment_id,
        "scenarios": [dict(item) for item in scenarios],
        "limitations": list(payload.get("limitations", [])),
        "report_sha256": declared_hash,
    }


def build_integrated_research_qa_report(
    experiment_path: str,
    *,
    qa_id: str,
    created_at: str,
    repository_commit: str,
    bundle: Any | None = None,
    cost_stress_report: Mapping[str, Any] | None = None,
    fill_stress_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble independently verified P10.2-P10.6 outputs without rerunning scenarios."""
    base = build_research_qa_report(
        experiment_path, qa_id=qa_id, created_at=created_at,
        repository_commit=repository_commit, bundle=bundle,
    )
    from ashare_quant.research.fill_stress import build_fill_stress_assessment
    from ashare_quant.research.statistical_qa import build_statistical_qa_summary

    execution_stress = build_fill_stress_assessment(experiment_path)
    statistical_qa = build_statistical_qa_summary(experiment_path)
    experiment_id = base["experiment_ids"][0]
    if execution_stress.get("experiment_id") != experiment_id:
        raise ValueError("Fill-stress assessment experiment identity does not match QA baseline")
    if statistical_qa.get("experiment_id") != experiment_id:
        raise ValueError("Statistical QA experiment identity does not match QA baseline")
    if execution_stress.get("admission_decision") != base["admission"]["decision"]:
        raise ValueError("Fill-stress admission does not match integrated QA admission")
    if statistical_qa.get("admission_decision") != base["admission"]["decision"]:
        raise ValueError("Statistical QA admission does not match integrated QA admission")

    if fill_stress_report is None:
        fill_section = {
            "status": "NOT_PROVIDED", "baseline_experiment_id": experiment_id,
            "scenarios": [],
            "limitations": ["No verified P10.5 F0-F4 fill-stress report was supplied."],
        }
    else:
        if not isinstance(fill_stress_report, Mapping):
            raise TypeError("fill_stress_report must be a verified P10.5 report mapping")
        fill_section = _verified_fill_stress_section(
            fill_stress_report, baseline_experiment_id=experiment_id,
            repository_commit=repository_commit,
            admission_decision=base["admission"]["decision"],
        )
        execution_stress = {
            "protocol_version": "phase10.5",
            "experiment_id": experiment_id,
            "admission_decision": base["admission"]["decision"],
            "scenarios": fill_section["scenarios"],
            "model_training_performed": False,
            "training_dataset_unlocked": False,
            "limitations": fill_section["limitations"],
            "report_sha256": fill_section["report_sha256"],
        }

    if cost_stress_report is None:
        cost_section = {
            "status": "NOT_PROVIDED", "baseline_experiment_id": experiment_id,
            "scenarios": [],
            "limitations": ["No verified P10.4 cost-stress report was supplied."],
        }
    else:
        if not isinstance(cost_stress_report, Mapping):
            raise TypeError("cost_stress_report must be a verified P10.4 report mapping")
        if cost_stress_report.get("protocol_version") != "phase10.4":
            raise ValueError("cost_stress_report protocol_version must be phase10.4")
        if base["admission"]["decision"] != "PRICE_ONLY_ELIGIBLE":
            raise ValueError("A blocked baseline cannot be combined with a verified P10.4 cost report")
        if cost_stress_report.get("baseline_experiment_id") != experiment_id:
            raise ValueError("P10.4 cost-stress baseline does not match the QA experiment")
        if cost_stress_report.get("repository_commit") != repository_commit.lower():
            raise ValueError("P10.4 cost-stress repository commit does not match the QA report")
        integrity = cost_stress_report.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("all_artifacts_verified") is not True:
            raise ValueError("P10.4 cost-stress artifacts are not all verified")
        if cost_stress_report.get("training_dataset_unlocked") is not False or cost_stress_report.get("model_training_performed") is not False:
            raise ValueError("P10.4 cost-stress report violates the training lock")
        scenarios = cost_stress_report.get("scenarios")
        if not isinstance(scenarios, list) or not all(isinstance(item, Mapping) for item in scenarios):
            raise ValueError("P10.4 cost-stress scenarios must be structured records")
        if [item.get("scenario_id") for item in scenarios] != ["C0", "C1", "C2", "C3"]:
            raise ValueError("P10.4 cost-stress report must contain verified C0-C3 scenarios")
        decisions = integrity.get("admission_decisions")
        if not isinstance(decisions, list) or len(decisions) != 4 or any(item != "PRICE_ONLY_ELIGIBLE" for item in decisions):
            raise ValueError("P10.4 cost-stress admission was not PRICE_ONLY_ELIGIBLE for every scenario")
        experiment_ids = cost_stress_report.get("experiment_ids")
        if not isinstance(experiment_ids, list) or not experiment_ids or experiment_ids[0] != experiment_id:
            raise ValueError("P10.4 cost-stress experiment IDs do not include the QA baseline")
        declared_hash = integrity.get("report_sha256")
        if not isinstance(declared_hash, str) or len(declared_hash) != 64:
            raise ValueError("P10.4 cost-stress report hash is missing or invalid")
        unhashed = dict(cost_stress_report)
        unhashed_integrity = dict(integrity)
        unhashed_integrity.pop("report_sha256", None)
        unhashed["integrity"] = unhashed_integrity
        actual_hash = hashlib.sha256(
            json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if actual_hash != declared_hash:
            raise ValueError("P10.4 cost-stress report content does not match its SHA-256")
        cost_section = {
            "status": "VERIFIED", "baseline_experiment_id": experiment_id,
            "scenarios": list(scenarios),
            "limitations": list(cost_stress_report.get("limitations", [])),
            "report_sha256": integrity.get("report_sha256"),
        }

    report = dict(base)
    report["protocol_version"] = "phase10.7"
    report["cost_stress"] = cost_section
    report["fill_stress"] = fill_section
    report["execution_stress"] = execution_stress
    report["statistical_qa"] = statistical_qa
    report["scenarios"] = [
        *[{"scenario_type": "cost_stress", **item} for item in cost_section["scenarios"]],
        *[{"scenario_type": "fill_stress", **item} for item in execution_stress["scenarios"]],
    ]
    report["limitations"] = list(base["limitations"])
    report["limitations"].extend(cost_section["limitations"])
    report["limitations"].extend(fill_section["limitations"])
    report["limitations"].extend(execution_stress["limitations"])
    report["limitations"].extend(statistical_qa["limitations"])
    if bundle is None:
        report["limitations"].append(
            "P10.3 period/regime diagnostics were not supplied because no canonical bundle was provided."
        )
    report["integrity"] = dict(base["integrity"])
    for name, payload in (
        ("fill_stress_sha256", execution_stress),
        ("statistical_qa_sha256", statistical_qa),
    ):
        report["integrity"][name] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
    if cost_section["status"] == "VERIFIED":
        report["integrity"]["cost_stress_sha256"] = cost_section["report_sha256"]
    if fill_section["status"] == "VERIFIED":
        report["integrity"]["fill_stress_suite_sha256"] = fill_section["report_sha256"]
    report["conclusion"] = (
        "BLOCKED" if base["admission"]["decision"] == "BLOCKED" else "INSUFFICIENT_EVIDENCE"
    )
    validate_research_qa_report(report)
    return report


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Integrated Research QA Report", "",
        f"- QA ID: {report['qa_id']}",
        f"- Created at: {report['created_at']}",
        f"- Repository commit: {report['repository_commit']}",
        f"- Experiment: {', '.join(report['experiment_ids'])}",
        f"- Admission: {report['admission']['decision']}",
        f"- Conclusion: {report['conclusion']}", "",
        "## Integrity", "",
        f"- Verified: {report['integrity']['verified']}",
        f"- Manifest SHA-256: {report['integrity']['manifest_sha256']}", "",
        "## Cost stress", "",
        f"- Status: {report['cost_stress']['status']}",
    ]
    for item in report["cost_stress"]["scenarios"]:
        lines.append(
            f"- {item['scenario_id']}: multiplier={item['cost_multiplier']}x, "
            f"net_return={item['net_return']:.6f}, CAGR={item['cagr']:.6f}"
        )
    lines.extend(["", "## Fill stress", ""])
    for item in report["execution_stress"]["scenarios"]:
        lines.append(
            f"- {item['scenario_id']}: target={item['target_fill_ratio']:.0%}, status={item['status']}"
        )
    lines.extend(["", "## Statistical QA", ""])
    lines.append(f"- Decision: {report['statistical_qa']['decision']}")
    lines.append(f"- Positive fold ratio: {report['statistical_qa']['positive_fold_ratio']}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def write_research_qa_report(
    report: Mapping[str, Any],
    *,
    output_root: str | Path,
) -> dict[str, str]:
    """Write a new QA-ID directory; never overwrite an existing report."""
    validate_research_qa_report(report)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / str(report["qa_id"])
    destination.mkdir(exist_ok=False)
    json_path = destination / "report.json"
    markdown_path = destination / "report.md"
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    markdown = _render_markdown(report)
    with json_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    with markdown_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown)
    return {
        "directory": str(destination),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
    }
