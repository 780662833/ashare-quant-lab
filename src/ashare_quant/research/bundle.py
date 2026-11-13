"""Canonical, fail-closed inputs for A-share research runs.

This module does not call CNEquity. Provider-specific data is normalized by
adapters first, then validated and joined here. It never constructs a training
dataset and never unlocks model training.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest.engine import weekly_rebalance_dates
from ashare_quant.exceptions import ResearchBundleError

RAW_COLUMNS = {
    "instrument", "date", "open", "high", "low", "close", "volume", "amount",
}
EXECUTION_EVIDENCE_COLUMNS = {
    "instrument", "date", "is_suspended", "is_limit_up_locked",
    "is_limit_down_locked", "limit_up", "limit_down", "risk_warning_known",
    "limit_evidence_source", "limit_rule_version", "limit_regime",
}
ADJUSTED_COLUMNS = {"instrument", "date", "adj_close", "adj_is_exact"}
UNIVERSE_COLUMNS = {"instrument", "date"}
REQUIRED_BENCHMARK_GATES = {
    "historical_universe", "hfq_adjustment", "trading_status",
}
REQUIRED_ACCEPTANCE_GATES = {
    *REQUIRED_BENCHMARK_GATES,
    "historical_universe_receipt",
    "point_in_time_fundamentals",
}
ENGINE_COLUMNS = [
    "instrument", "date", "open", "high", "low", "close", "volume", "amount",
    "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "limit_up", "limit_down",
]



REQUIRED_RECEIPT_DEPENDENCIES = {"daily_bars", "adj_factors", "trading_status", "trading_calendar"}
REQUIRED_COVERAGE_AUDITS = {"daily_bars", "adj_factors", "universe", "trading_status", "trading_calendar", "price_limit_rules"}


def _validate_source_provenance(source_receipts, source_revisions):
    """Bind declared source identities to actual adapter receipt dependencies."""
    if not isinstance(source_receipts, list) or not source_receipts:
        raise ResearchBundleError("source_receipts must contain adapter receipt records")
    observed = {}
    receipt_ids = []
    completeness = {}
    for record in source_receipts:
        if not isinstance(record, Mapping):
            raise ResearchBundleError("source_receipts contains a malformed adapter record")
        dataset, receipt = record.get("dataset"), record.get("receipt")
        if not isinstance(dataset, str) or not dataset.strip() or not isinstance(receipt, Mapping):
            raise ResearchBundleError("source_receipts record requires dataset and receipt")
        if receipt.get("replayable") is not True:
            raise ResearchBundleError(f"Adapter receipt for {dataset} is not replayable")
        if isinstance(receipt.get("receipt_id"), str) and receipt["receipt_id"]:
            receipt_ids.append(receipt["receipt_id"])
        coverage = receipt.get("coverage")
        if isinstance(coverage, Mapping):
            completeness[dataset] = coverage.get("completeness", "unknown")
        dependencies = receipt.get("dependencies")
        if not isinstance(dependencies, Mapping) or not dependencies:
            raise ResearchBundleError(f"Adapter receipt for {dataset} has no dependency identities")
        for name, identity in dependencies.items():
            if not isinstance(identity, Mapping):
                raise ResearchBundleError(f"Receipt dependency {name} is malformed")
            revision_id, fingerprint = identity.get("revision_id"), identity.get("contract_fingerprint")
            if not isinstance(revision_id, (str, int)) or not str(revision_id):
                raise ResearchBundleError(f"Receipt dependency {name} has no revision_id")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                raise ResearchBundleError(f"Receipt dependency {name} has no contract_fingerprint")
            value = {"revision_id": revision_id, "contract_fingerprint": fingerprint}
            if name in observed and observed[name] != value:
                raise ResearchBundleError(f"Adapter receipts disagree on dependency identity for {name}")
            observed[str(name)] = value

    missing = sorted(REQUIRED_RECEIPT_DEPENDENCIES.difference(observed))
    if missing:
        raise ResearchBundleError("Adapter receipts do not cover required dependencies: " + ", ".join(missing))
    if not isinstance(source_revisions, Mapping):
        raise ResearchBundleError("source_revisions must be structured identity mappings")
    required = {"daily_bars", "adj_factors", "universe", "trading_status", "trading_calendar", "price_limit_rules"}
    missing = sorted(required.difference(source_revisions))
    if missing:
        raise ResearchBundleError(f"source_revisions missing required datasets: {missing}")

    normalized = {}
    for name in sorted(required):
        identity = source_revisions.get(name)
        if not isinstance(identity, Mapping):
            raise ResearchBundleError(f"source_revisions[{name}] must be a receipt-bound identity mapping")
        revision_id, fingerprint = identity.get("revision_id"), identity.get("contract_fingerprint")
        if not isinstance(revision_id, (str, int)) or not str(revision_id):
            raise ResearchBundleError(f"source_revisions[{name}] has no revision_id")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ResearchBundleError(f"source_revisions[{name}] has no contract_fingerprint")
        value = {"revision_id": revision_id, "contract_fingerprint": fingerprint}
        if name == "universe":
            actual = observed.get("daily_bars")
        elif name == "price_limit_rules":
            value["source"] = str(identity.get("source", "")).strip()
            if not value["source"]:
                raise ResearchBundleError("price_limit_rules identity requires a source")
            actual = None
        else:
            actual = observed.get(name)
        if name != "price_limit_rules" and actual is None:
            raise ResearchBundleError(f"source_revisions[{name}] has no matching adapter receipt dependency")
        if actual is not None and value != actual:
            raise ResearchBundleError(f"source_revisions[{name}] does not match adapter receipt dependency identity")
        normalized[name] = value
    return normalized, {
        "adapter_receipt_ids": sorted(set(receipt_ids)),
        "dependency_identities": observed,
        "coverage_completeness": completeness,
    }


def _validate_coverage_audits(audits, *, start_date, end_date, observed_rows):
    """Require dataset-specific completeness evidence; row presence is insufficient."""
    if not isinstance(audits, Mapping):
        raise ResearchBundleError("coverage_audits must be a dataset-keyed mapping")
    missing = sorted(REQUIRED_COVERAGE_AUDITS.difference(audits))
    if missing:
        raise ResearchBundleError(f"coverage_audits missing required datasets: {missing}")
    normalized = {}
    for dataset in sorted(REQUIRED_COVERAGE_AUDITS):
        audit = audits.get(dataset)
        if not isinstance(audit, Mapping) or audit.get("status") != "PASS":
            raise ResearchBundleError(f"coverage audit for {dataset} is not PASS")
        if audit.get("window") != {"start": start_date, "end": end_date}:
            raise ResearchBundleError(f"coverage audit window mismatch for {dataset}")
        for field_name in ("expected_rows", "observed_rows", "missing_keys", "unexpected_keys"):
            value = audit.get(field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ResearchBundleError(f"coverage audit for {dataset} has invalid {field_name}")
        if audit["expected_rows"] != audit["observed_rows"]:
            raise ResearchBundleError(f"coverage audit row-count mismatch for {dataset}")
        if audit["missing_keys"] or audit["unexpected_keys"]:
            raise ResearchBundleError(f"coverage audit has key gaps for {dataset}")
        for field_name in ("audit_id", "method", "source"):
            if not isinstance(audit.get(field_name), str) or not audit[field_name].strip():
                raise ResearchBundleError(f"coverage audit for {dataset} requires {field_name}")
        if dataset in observed_rows and audit["observed_rows"] != observed_rows[dataset]:
            raise ResearchBundleError(f"coverage audit observed_rows does not match assembled {dataset} rows")
        normalized[dataset] = {key: audit[key] for key in (
            "audit_id", "status", "window", "expected_rows", "observed_rows",
            "missing_keys", "unexpected_keys", "method", "source",
        )}
    return normalized


@dataclass(frozen=True, slots=True)
class ResearchDataBundle:
    """Validated and provenance-bound data passed to strategies and the engine."""

    execution_bars: pl.DataFrame
    adjusted_bars: pl.DataFrame
    universe_membership: pl.DataFrame
    trading_dates: list[str]
    provenance: dict[str, Any]


def _iso_date(value: Any, *, field: str) -> str:
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ResearchBundleError(f"{field} contains invalid ISO date {text!r}") from exc
    if parsed.isoformat() != text:
        raise ResearchBundleError(f"{field} must use YYYY-MM-DD, got {text!r}")
    return text


def _date_window(start_date: str, end_date: str) -> tuple[str, str]:
    start = _iso_date(start_date, field="start_date")
    end = _iso_date(end_date, field="end_date")
    if start > end:
        raise ResearchBundleError("start_date must be on or before end_date")
    return start, end


def _keyed_frame(
    frame: pl.DataFrame,
    *,
    name: str,
    required_columns: set[str],
    start_date: str,
    end_date: str,
) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame):
        raise ResearchBundleError(f"{name} must be a Polars DataFrame")
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ResearchBundleError(f"{name} missing columns: {sorted(missing)}")
    if frame.is_empty():
        raise ResearchBundleError(f"{name} must not be empty")

    normalized = frame.with_columns(
        pl.col("instrument").cast(pl.String, strict=True),
        pl.col("date").cast(pl.String, strict=True),
    )
    if normalized.select(
        pl.any_horizontal(pl.col("instrument").is_null(), pl.col("date").is_null()).any()
    ).item():
        raise ResearchBundleError(f"{name} contains null instrument/date keys")
    if normalized.select(
        pl.struct(["instrument", "date"]).is_duplicated().any()
    ).item():
        raise ResearchBundleError(f"{name} contains duplicate instrument/date keys")

    values = normalized["date"].unique().to_list()
    for value in values:
        normalized_date = _iso_date(value, field=f"{name}.date")
        if not start_date <= normalized_date <= end_date:
            raise ResearchBundleError(
                f"{name} contains date outside requested window: {normalized_date}"
            )
    return normalized


def _require_gates(
    report: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    universe_profile: str,
) -> dict[str, str]:
    if not isinstance(report, Mapping):
        raise ResearchBundleError("acceptance_report must be a mapping")
    if report.get("window") != {"start": start_date, "end": end_date}:
        raise ResearchBundleError("Acceptance report window does not match bundle window")
    if report.get("profile") != universe_profile:
        raise ResearchBundleError("Acceptance report universe profile does not match")
    if report.get("training_dataset_unlocked") is not False:
        raise ResearchBundleError(
            "Research bundle must not unlock training; expected training_dataset_unlocked=false"
        )

    gates = report.get("gates")
    if not isinstance(gates, list):
        raise ResearchBundleError("Acceptance report is missing its gates list")
    gate_map: dict[str, str] = {}
    for gate in gates:
        if not isinstance(gate, Mapping) or not gate.get("name"):
            raise ResearchBundleError("Acceptance report contains a malformed gate")
        name = str(gate["name"])
        if name in gate_map:
            raise ResearchBundleError(f"Acceptance report has duplicate gate {name!r}")
        gate_map[name] = str(gate.get("status", "FAIL")).upper()

    missing = sorted(REQUIRED_ACCEPTANCE_GATES.difference(gate_map))
    if missing:
        raise ResearchBundleError(
            "Acceptance report is missing independent gates: " + ", ".join(missing)
        )

    failed = sorted(
        name for name in REQUIRED_BENCHMARK_GATES if gate_map.get(name) != "PASS"
    )
    if gate_map["historical_universe_receipt"] != "PASS":
        failed.append("historical_universe_receipt")
    if failed:
        raise ResearchBundleError(
            "Benchmark data admission failed these required gates: " + ", ".join(failed)
        )

    receipts = report.get("receipts")
    if not isinstance(receipts, Mapping):
        raise ResearchBundleError("Acceptance report is missing its source receipts")
    for receipt_name in ("historical_universe", "hfq"):
        receipt = receipts.get(receipt_name)
        if (
            not isinstance(receipt, Mapping)
            or not receipt
            or receipt.get("replayable") is not True
        ):
            raise ResearchBundleError(
                f"Acceptance report is missing replayable {receipt_name} receipt"
            )
    return {name: gate_map[name] for name in sorted(REQUIRED_ACCEPTANCE_GATES)}


def build_research_data_bundle(
    *,
    raw_bars: pl.DataFrame,
    adjusted_bars: pl.DataFrame,
    universe_membership: pl.DataFrame,
    execution_evidence: pl.DataFrame,
    trading_dates: list[Any],
    acceptance_report: Mapping[str, Any],
    acceptance_report_id: str,
    source_revisions: Mapping[str, Mapping[str, Any]],
    source_receipts: list[Mapping[str, Any]],
    coverage_audits: Mapping[str, Mapping[str, Any]],
    start_date: str,
    end_date: str,
    universe_profile: str,
) -> ResearchDataBundle:
    """Validate and assemble the canonical benchmark research bundle.

    Momentum admission requires historical-universe, HFQ, and trading-status
    gates to PASS. Fundamental PIT is not consumed by this price-only
    benchmark; a future model-training builder must enforce all four gates
    independently and is deliberately not implemented here.

    Missing status, adjusted price, weekly membership, or calendar coverage
    is a hard failure. No missing execution state or adjusted value is inferred.
    """
    start, end = _date_window(start_date, end_date)
    if not universe_profile:
        raise ResearchBundleError("universe_profile must be non-empty")
    if not acceptance_report_id or not acceptance_report_id.strip():
        raise ResearchBundleError("acceptance_report_id must be non-empty")
    revisions, receipt_provenance = _validate_source_provenance(
        source_receipts, source_revisions
    )

    required_gate_status = _require_gates(
        acceptance_report,
        start_date=start,
        end_date=end,
        universe_profile=universe_profile,
    )

    normalized_sessions = [_iso_date(value, field="trading_dates") for value in trading_dates]
    if len(normalized_sessions) != len(set(normalized_sessions)):
        raise ResearchBundleError("trading_dates must contain unique sessions")
    sessions = sorted(normalized_sessions)
    if len(sessions) < 2:
        raise ResearchBundleError("trading_dates must contain at least two sessions")
    if sessions[0] < start or sessions[-1] > end:
        raise ResearchBundleError("trading_dates fall outside the requested window")
    weekly_dates = weekly_rebalance_dates(sessions)

    raw = _keyed_frame(
        raw_bars, name="raw_bars", required_columns=RAW_COLUMNS,
        start_date=start, end_date=end,
    )
    adjusted = _keyed_frame(
        adjusted_bars, name="adjusted_bars", required_columns=ADJUSTED_COLUMNS,
        start_date=start, end_date=end,
    )
    membership = _keyed_frame(
        universe_membership, name="universe_membership",
        required_columns=UNIVERSE_COLUMNS, start_date=start, end_date=end,
    )
    state = _keyed_frame(
        execution_evidence, name="execution_evidence", required_columns=EXECUTION_EVIDENCE_COLUMNS,
        start_date=start, end_date=end,
    )

    raw_numeric = ["open", "high", "low", "close", "volume", "amount"]
    try:
        raw = raw.with_columns([pl.col(column).cast(pl.Float64, strict=True) for column in raw_numeric])
        adjusted = adjusted.with_columns(
            pl.col("adj_close").cast(pl.Float64, strict=True),
            pl.col("adj_is_exact").cast(pl.Boolean, strict=True),
        )
        state = state.with_columns([
            *[
                pl.col(column).cast(pl.Boolean, strict=True)
                for column in (
                    "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "risk_warning_known",
                )
            ],
            pl.col("limit_up").cast(pl.Float64, strict=True),
            pl.col("limit_down").cast(pl.Float64, strict=True),
        ])
    except Exception as exc:
        raise ResearchBundleError(f"Canonical inputs contain non-canonical value types: {exc}") from exc

    if raw.select(pl.any_horizontal([pl.col(c).is_null() for c in raw_numeric]).any()).item():
        raise ResearchBundleError("raw_bars contains null OHLCV values")
    invalid_raw = (
        (pl.col("open") <= 0) | (pl.col("high") <= 0) |
        (pl.col("low") <= 0) | (pl.col("close") <= 0) |
        (pl.col("high") < pl.max_horizontal("open", "close", "low")) |
        (pl.col("low") > pl.min_horizontal("open", "close", "high")) |
        (pl.col("volume") < 0) | (pl.col("amount") < 0) |
        pl.any_horizontal([~pl.col(c).is_finite() for c in raw_numeric])
    )
    if raw.select(invalid_raw.any()).item():
        raise ResearchBundleError("raw_bars contains invalid OHLCV values")

    if adjusted.select(
        pl.any_horizontal(pl.col("adj_close").is_null(), pl.col("adj_is_exact").is_null()).any()
    ).item():
        raise ResearchBundleError("adjusted_bars contains null adjusted price/exactness")
    if adjusted.filter(
        (~pl.col("adj_close").is_finite()) |
        (pl.col("adj_close") <= 0) |
        (pl.col("adj_is_exact") != True)
    ).height:
        raise ResearchBundleError("adjusted_bars contains non-positive or non-exact HFQ values")

    status_flags = [
        "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "risk_warning_known",
    ]
    if state.select(pl.any_horizontal([pl.col(c).is_null() for c in status_flags]).any()).item():
        raise ResearchBundleError("execution_evidence contains unknown/null state flags")
    if state.filter(~pl.col("risk_warning_known")).height:
        raise ResearchBundleError("execution_evidence contains unknown risk-warning state")
    evidence_text = ["limit_evidence_source", "limit_rule_version", "limit_regime"]
    if state.select(pl.any_horizontal([pl.col(c).is_null() for c in evidence_text]).any()).item():
        raise ResearchBundleError("execution_evidence has missing limit source/version provenance")
    if state.filter(
        pl.any_horizontal([pl.col(c).cast(pl.String).str.strip_chars().eq("") for c in evidence_text])
    ).height:
        raise ResearchBundleError("execution_evidence has empty limit source/version provenance")
    if state.filter(
        (pl.col("limit_up").is_not_null() & (
            (~pl.col("limit_up").is_finite()) | (pl.col("limit_up") <= 0)
        )) |
        (pl.col("limit_down").is_not_null() & (
            (~pl.col("limit_down").is_finite()) | (pl.col("limit_down") <= 0)
        ))
    ).height:
        raise ResearchBundleError("execution_evidence contains invalid limit prices")
    if state.filter(
        pl.col("is_limit_up_locked") & pl.col("limit_up").is_null()
    ).height:
        raise ResearchBundleError("Limit-up lock flag lacks a limit-up price")
    if state.filter(
        pl.col("is_limit_down_locked") & pl.col("limit_down").is_null()
    ).height:
        raise ResearchBundleError("Limit-down lock flag lacks a limit-down price")
    if state.filter(
        pl.col("is_limit_up_locked") & pl.col("is_limit_down_locked")
    ).height:
        raise ResearchBundleError("A row cannot be locked at both limit-up and limit-down")
    declared_limit_source = revisions["price_limit_rules"]["source"]
    observed_limit_sources = set(state["limit_evidence_source"].cast(pl.String).unique().to_list())
    if observed_limit_sources != {declared_limit_source}:
        raise ResearchBundleError(
            "price_limit_rules source identity does not match row-level limit evidence provenance"
        )

    session_set = set(sessions)
    membership_dates = set(membership["date"].to_list())
    if not membership_dates.issubset(session_set):
        raise ResearchBundleError("universe_membership contains dates outside trading calendar")

    status_dates = set(state["date"].to_list())
    if not status_dates.issubset(session_set):
        raise ResearchBundleError("trading_status contains dates outside trading calendar")

    raw_dates = set(raw["date"].to_list())
    if raw_dates != set(sessions):
        missing = sorted(set(sessions).difference(raw_dates))
        unexpected = sorted(raw_dates.difference(sessions))
        raise ResearchBundleError(
            f"raw_bars calendar coverage mismatch; missing={missing}, unexpected={unexpected}"
        )

    raw_keys = raw.select("instrument", "date")
    adjusted_keys = adjusted.select("instrument", "date")
    missing_adjusted = raw_keys.join(adjusted_keys, on=["instrument", "date"], how="anti")
    extra_adjusted = adjusted_keys.join(raw_keys, on=["instrument", "date"], how="anti")
    if missing_adjusted.height or extra_adjusted.height:
        raise ResearchBundleError(
            "raw/adjusted key coverage differs; "
            f"missing_adjusted={missing_adjusted.height}, extra_adjusted={extra_adjusted.height}"
        )

    raw_instruments = set(raw["instrument"].to_list())
    unknown_members = sorted(set(membership["instrument"].to_list()).difference(raw_instruments))
    if unknown_members:
        raise ResearchBundleError(
            f"universe_membership contains instruments outside raw market scope: {unknown_members[:20]}"
        )
    missing_weekly_membership = sorted(
        set(weekly_dates).difference(set(membership["date"].to_list()))
    )
    if missing_weekly_membership:
        raise ResearchBundleError(
            "Point-in-time membership missing weekly signal dates: "
            f"{missing_weekly_membership}"
        )

    state_keys = state.select("instrument", "date")
    raw_keys_status = raw_keys.join(
        state_keys, on=["instrument", "date"], how="anti"
    )
    extra_state = state_keys.join(
        raw_keys, on=["instrument", "date"], how="anti"
    )
    if raw_keys_status.height or extra_state.height:
        raise ResearchBundleError(
            "execution_evidence key scope differs from raw bars; "
            f"missing={raw_keys_status.height}, extra={extra_state.height}"
        )
    joined = raw.join(state, on=["instrument", "date"], how="left")
    if joined.select(pl.any_horizontal([pl.col(c).is_null() for c in status_flags]).any()).item():
        raise ResearchBundleError("Joined execution rows contain missing trading-state evidence")
    if joined.filter(pl.col("is_suspended") & (pl.col("volume") > 0)).height:
        raise ResearchBundleError("Trading-status contradiction: suspended security has positive volume")

    execution_bars = joined.select(ENGINE_COLUMNS).sort(["date", "instrument"])
    adjusted_out = adjusted.select(
        "instrument", "date", "adj_close", "adj_is_exact"
    ).sort(["date", "instrument"])
    membership_out = membership.select("instrument", "date").sort(["date", "instrument"])

    coverage_audit_provenance = _validate_coverage_audits(
        coverage_audits,
        start_date=start,
        end_date=end,
        observed_rows={
            "daily_bars": raw.height,
            "adj_factors": adjusted_out.height,
            "universe": membership_out.height,
            "trading_status": state.height,
            "trading_calendar": len(sessions),
        },
    )

    provenance = {
        "acceptance_report_id": acceptance_report_id,
        "acceptance_report_created_at": acceptance_report.get("created_at"),
        "acceptance_report_window": {"start": start, "end": end},
        "universe_profile": universe_profile,
        "required_benchmark_gates": {
            name: required_gate_status[name]
            for name in sorted(REQUIRED_BENCHMARK_GATES)
        },
        "acceptance_gates": required_gate_status,
        "pit_fundamentals_used": False,
        "training_dataset_unlocked": False,
        "source_revisions": revisions,
        "source_receipts": source_receipts,
        "acceptance_source_receipts": acceptance_report.get("receipts", {}),
        "receipt_provenance": receipt_provenance,
        "coverage_audits": coverage_audit_provenance,
        "limit_evidence": {
            "sources": sorted(state["limit_evidence_source"].unique().to_list()),
            "rule_versions": sorted(state["limit_rule_version"].unique().to_list()),
            "regimes": sorted(state["limit_regime"].unique().to_list()),
            "revision": revisions["price_limit_rules"]["revision_id"],
            "source_revision_identity": revisions["price_limit_rules"],
        },
        "scope": {
            "start_date": start,
            "end_date": end,
            "trading_sessions": len(sessions),
            "raw_bar_rows": raw.height,
            "adjusted_bar_rows": adjusted_out.height,
            "universe_membership_rows": membership_out.height,
            "instruments": len(raw_instruments),
            "weekly_signal_dates": weekly_dates,
        },
    }
    return ResearchDataBundle(
        execution_bars=execution_bars,
        adjusted_bars=adjusted_out,
        universe_membership=membership_out,
        trading_dates=sessions,
        provenance=provenance,
    )
