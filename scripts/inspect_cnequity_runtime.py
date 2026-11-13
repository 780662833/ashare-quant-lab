"""Read-only CNEquity runtime/API preflight for Phase 10.5.

This script inspects the installed package and the adapter's imported API surface.
It does not read the data lake, issue network requests, run backfills, or modify files.
An API/version match is a prerequisite check, not proof of data-contract compatibility.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_APIS: dict[str, tuple[str, ...]] = {
    "cnequity.query": ("load", "load_with_receipt", "resolve_config"),
    "cnequity.quality.factor_diagnostics": ("publish_factor_diagnostics",),
    "cnequity.quality.historical_validity": ("historical_universe_validity",),
    "cnequity.quality.trading_status_coverage": ("traded_status_evidence",),
}


def _declared_cnequity_requirement() -> str | None:
    project_file = ROOT / "pyproject.toml"
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for requirement in project.get("project", {}).get("dependencies", []):
        if requirement.lower().startswith("cnequity"):
            return requirement
    return None


def _signature(value: Any) -> str | None:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return None


def inspect_runtime() -> dict[str, Any]:
    declared = _declared_cnequity_requirement()
    try:
        installed = importlib.metadata.version("cnequity")
    except importlib.metadata.PackageNotFoundError:
        installed = None

    report: dict[str, Any] = {
        "schema_version": 1,
        "check": "phase10_5_cnequity_runtime_preflight",
        "read_only": True,
        "network_access": False,
        "data_lake_access": False,
        "declared_requirement": declared,
        "installed_version": installed,
        "version_exact_match": bool(
            installed and declared and declared.lower() == f"cnequity=={installed}".lower()
        ),
        "modules": {},
        "missing_apis": [],
        "import_errors": [],
        "compatibility_status": "UNVERIFIED",
        "limitations": [
            "Import/signature inspection does not validate runtime behavior or data contracts.",
            "No local CNEquity datasets, receipts, coverage, or source revisions are inspected.",
            "A version mismatch must not be treated as compatible merely because symbols exist.",
        ],
    }

    for module_name, symbols in REQUIRED_APIS.items():
        module_report: dict[str, Any] = {"imported": False, "symbols": {}}
        try:
            module = importlib.import_module(module_name)
            module_report["imported"] = True
            module_report["origin"] = getattr(module, "__file__", None)
            for symbol in symbols:
                value = getattr(module, symbol, None)
                present = callable(value)
                module_report["symbols"][symbol] = {
                    "callable": present,
                    "signature": _signature(value) if present else None,
                }
                if not present:
                    report["missing_apis"].append(f"{module_name}.{symbol}")
        except Exception as exc:  # diagnostic output must preserve the failing import
            module_report["error"] = f"{type(exc).__name__}: {exc}"
            report["import_errors"].append(module_name)
        report["modules"][module_name] = module_report

    api_surface_ok = not report["missing_apis"] and not report["import_errors"]
    if installed is None:
        report["compatibility_status"] = "PACKAGE_MISSING"
    elif not api_surface_ok:
        report["compatibility_status"] = "API_INCOMPLETE"
    elif not report["version_exact_match"]:
        report["compatibility_status"] = "VERSION_MISMATCH_UNVERIFIED"
    else:
        report["compatibility_status"] = "VERSION_MATCH_API_PRESENT_DATA_UNVERIFIED"
    return report


def main() -> int:
    report = inspect_runtime()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["compatibility_status"] == "VERSION_MATCH_API_PRESENT_DATA_UNVERIFIED":
        return 0
    if report["compatibility_status"] == "PACKAGE_MISSING":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
