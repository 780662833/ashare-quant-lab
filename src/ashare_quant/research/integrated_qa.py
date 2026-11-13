"""P10.7 integrated, write-once QA report assembly."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ashare_quant.research.qa import (
    build_research_qa_report,
    validate_research_qa_report,
)

def build_integrated_research_qa_report(
    experiment_path: str,
    *,
    qa_id: str,
    created_at: str,
    repository_commit: str,
    bundle: Any | None = None,
    cost_stress_report: Mapping[str, Any] | None = None,
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
        if not isinstance(scenarios, list) or [item.get("scenario_id") for item in scenarios] != ["C0", "C1", "C2", "C3"]:
            raise ValueError("P10.4 cost-stress report must contain verified C0-C3 scenarios")
        decisions = integrity.get("admission_decisions")
        if not isinstance(decisions, list) or any(item != "PRICE_ONLY_ELIGIBLE" for item in decisions):
            raise ValueError("P10.4 cost-stress admission was not PRICE_ONLY_ELIGIBLE for every scenario")
        cost_section = {
            "status": "VERIFIED", "baseline_experiment_id": experiment_id,
            "scenarios": list(scenarios),
            "limitations": list(cost_stress_report.get("limitations", [])),
            "report_sha256": integrity.get("report_sha256"),
        }

    report = dict(base)
    report["protocol_version"] = "phase10.7"
    report["cost_stress"] = cost_section
    report["execution_stress"] = execution_stress
    report["statistical_qa"] = statistical_qa
    report["scenarios"] = [
        *[{"scenario_type": "cost_stress", **item} for item in cost_section["scenarios"]],
        *[{"scenario_type": "fill_stress", **item} for item in execution_stress["scenarios"]],
    ]
    report["limitations"] = list(base["limitations"])
    report["limitations"].extend(cost_section["limitations"])
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
    with markdown_path.open("x", encoding="utf-8", newline="\\n") as stream:
        stream.write(markdown)
    return {
        "directory": str(destination),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
    }
