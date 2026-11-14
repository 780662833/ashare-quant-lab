"""Fail-closed training dataset admission for the cross-sectional learner.

This module validates caller-supplied acceptance evidence and already-built
in-memory feature/label frames. It never reads a data lake, calls CNEquity,
fits a model, or changes the acceptance report's training lock.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

import polars as pl

from ashare_quant.research.experiment_provenance import validate_experiment_provenance

_REQUIRED_GATES = {
    "historical_universe",
    "historical_universe_receipt",
    "hfq_adjustment",
    "point_in_time_fundamentals",
    "trading_status",
}
_REQUIRED_FEATURE_KEYS = {"signal_date", "instrument"}
_REQUIRED_LABEL_COLUMNS = {
    "signal_date", "instrument", "label_end_date", "forward_return"
}


@dataclass(frozen=True, slots=True)
class TrainingDatasetAdmission:
    """Auditable decision; an admission is not a model-fit operation."""

    admitted: bool
    report_id: str
    start_date: str
    end_date: str
    universe_profile: str
    training_cutoff: str
    feature_rows: int
    label_rows: int
    feature_columns: tuple[str, ...]
    label_horizon_sessions: int
    source_fingerprint: str
    feature_fingerprint: str
    label_fingerprint: str
    rejection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso_date(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use canonical ISO YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use canonical YYYY-MM-DD format")
    return value


def _frame_fingerprint(frame: pl.DataFrame, *, keys: list[str]) -> str:
    """Hash a deterministic, schema-aware canonical row representation."""
    ordered = frame.sort(keys)
    payload = {
        "columns": ordered.columns,
        "schema": {name: str(dtype) for name, dtype in ordered.schema.items()},
        "rows": ordered.to_dicts(),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False, default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_acceptance(
    report: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    universe_profile: str,
    source_provenance: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if report.get("overall_status") != "PASS":
        reasons.append("acceptance report overall_status is not PASS")
    if report.get("training_dataset_unlocked") is not False:
        reasons.append("acceptance report must remain non-unlocking (false)")
    window = report.get("window")
    if not isinstance(window, Mapping) or (
        window.get("start") != start_date or window.get("end") != end_date
    ):
        reasons.append("acceptance report window does not exactly match requested window")
    if report.get("profile") != universe_profile:
        reasons.append("acceptance report universe profile does not match")

    gates = report.get("gates")
    if not isinstance(gates, list):
        reasons.append("acceptance report gates must be a list")
        gates = []
    gate_map: dict[str, Mapping[str, Any]] = {}
    for gate in gates:
        if not isinstance(gate, Mapping) or not isinstance(gate.get("name"), str):
            reasons.append("acceptance report contains a malformed gate")
            continue
        name = gate["name"]
        if name in gate_map:
            reasons.append(f"acceptance report duplicates gate {name}")
        gate_map[name] = gate
    for name in sorted(_REQUIRED_GATES):
        gate = gate_map.get(name)
        if gate is None:
            reasons.append(f"acceptance gate {name} is missing")
        elif gate.get("status") != "PASS":
            reasons.append(f"acceptance gate {name} is not PASS")

    universe = gate_map.get("historical_universe", {})
    supported = universe.get("supported_window") if isinstance(universe, Mapping) else None
    if not isinstance(supported, Mapping) or (
        supported.get("start") is None or supported.get("end") is None
        or str(supported.get("start")) > start_date
        or str(supported.get("end")) < end_date
    ):
        reasons.append("historical universe evidence does not cover the requested window")

    hfq = gate_map.get("hfq_adjustment", {})
    if isinstance(hfq, Mapping):
        if hfq.get("diagnostics_assessed") is not True:
            reasons.append("HFQ diagnostics were not assessed")
        if hfq.get("missing_factor_rows") != 0:
            reasons.append("HFQ evidence reports missing factor rows")
        if hfq.get("related_historical_events") != 0:
            reasons.append("HFQ evidence reports unresolved historical events")

    pit = gate_map.get("point_in_time_fundamentals", {})
    if isinstance(pit, Mapping):
        if pit.get("mode") != "strict":
            reasons.append("PIT fundamentals were not checked in strict mode")
        expected_items = report.get("pit_items")
        if not isinstance(expected_items, list) or sorted(pit.get("items", [])) != sorted(expected_items):
            reasons.append("PIT fundamental item coverage does not match the acceptance report")
        if pit.get("missing_symbol_item_pairs") != 0:
            reasons.append("PIT fundamentals have missing symbol/item coverage")

    trading = gate_map.get("trading_status", {})
    if isinstance(trading, Mapping):
        for field in (
            "missing_status_rows", "unknown_risk_warning_rows",
            "contradictory_rows", "excluded_or_unresolved_rows",
        ):
            if trading.get(field) != 0:
                reasons.append(f"trading status evidence has non-zero {field}")

    if not isinstance(report.get("report_id"), str) or not report["report_id"].strip():
        reasons.append("acceptance report requires a stable non-empty report_id")

    receipts = report.get("receipts")
    if not isinstance(receipts, Mapping):
        reasons.append("acceptance report requires structured source receipts")
        receipts = {}
    for receipt_name in ("historical_universe", "hfq", "pit"):
        receipt = receipts.get(receipt_name)
        if not isinstance(receipt, Mapping) or receipt.get("replayable") is not True:
            reasons.append(f"acceptance report {receipt_name} receipt is missing or not replayable")

    try:
        validate_experiment_provenance(source_provenance)
    except (TypeError, ValueError) as exc:
        reasons.append(f"source provenance is invalid: {exc}")
    else:
        if source_provenance.get("acceptance_report_id") != report.get("report_id"):
            reasons.append("source provenance acceptance_report_id does not match")
        if source_provenance.get("acceptance_report_window") != dict(window or {}):
            reasons.append("source provenance acceptance window does not match report")
        if source_provenance.get("universe_profile") != universe_profile:
            reasons.append("source provenance universe profile does not match")
        if source_provenance.get("acceptance_source_receipts") != dict(receipts):
            reasons.append("source provenance acceptance receipts do not match the report")
        pit_binding = source_provenance.get("pit_fundamentals")
        pit_revision = revisions.get("financial_statement_items") if isinstance(revisions, Mapping) else None
        pit_records = [
            record for record in source_provenance.get("source_receipts", [])
            if isinstance(record, Mapping) and record.get("dataset") == "financial_statement_items"
        ]
        if not isinstance(pit_binding, Mapping):
            reasons.append("source provenance is missing the strict PIT fundamentals binding")
        else:
            if sorted(pit_binding.get("items", [])) != sorted(report.get("pit_items", [])):
                reasons.append("strict PIT adapter item set does not match the acceptance report")
            if pit_binding.get("as_of_date") != end_date:
                reasons.append("strict PIT adapter as_of_date does not match acceptance window end")
            if pit_binding.get("availability_policy") != "announce_date_end_of_day_Asia/Shanghai":
                reasons.append("strict PIT availability policy is not the conservative end-of-day rule")
            if pit_binding.get("source_revision_identity") != pit_revision:
                reasons.append("strict PIT adapter revision identity does not match source revisions")
            if not pit_records:
                reasons.append("source provenance has no financial_statement_items adapter receipt")
            elif not any(
                isinstance(record.get("receipt"), Mapping)
                and record["receipt"].get("replayable") is True
                and record["receipt"].get("receipt_id") == pit_binding.get("receipt_id")
                and isinstance(record["receipt"].get("dependencies"), Mapping)
                and record["receipt"]["dependencies"].get("financial_statement_items") == pit_revision
                for record in pit_records
            ):
                reasons.append("strict PIT adapter receipt does not match its pinned revision")
    return reasons


def admit_training_dataset(
    *,
    acceptance_report: Mapping[str, Any],
    source_provenance: Mapping[str, Any],
    features: pl.DataFrame,
    labels: pl.DataFrame,
    start_date: str,
    end_date: str,
    training_cutoff: str,
    universe_profile: str,
    label_horizon_sessions: int = 5,
) -> TrainingDatasetAdmission:
    """Validate exact-scope acceptance, provenance and label maturity.

    All label_end_date values must be strictly earlier than training_cutoff.
    This is intentionally stricter than allowing a label to mature on the
    cutoff date. The returned object is an auditable admission decision only;
    it does not fit or unlock LightGBM.
    """
    start = _iso_date(start_date, "start_date")
    end = _iso_date(end_date, "end_date")
    cutoff = _iso_date(training_cutoff, "training_cutoff")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if cutoff <= start or cutoff > end:
        raise ValueError("training_cutoff must be after start_date and within end_date")
    if not isinstance(universe_profile, str) or not universe_profile.strip():
        raise ValueError("universe_profile must be non-empty")
    if isinstance(label_horizon_sessions, bool) or not isinstance(label_horizon_sessions, int) or label_horizon_sessions < 1:
        raise ValueError("label_horizon_sessions must be a positive integer")
    if not isinstance(acceptance_report, Mapping):
        raise TypeError("acceptance_report must be a mapping")
    if not isinstance(source_provenance, Mapping):
        raise TypeError("source_provenance must be a mapping")
    if not isinstance(features, pl.DataFrame) or not isinstance(labels, pl.DataFrame):
        raise TypeError("features and labels must be Polars DataFrames")

    reasons = _validate_acceptance(
        acceptance_report,
        start_date=start,
        end_date=end,
        universe_profile=universe_profile,
        source_provenance=source_provenance,
    )

    missing_feature = _REQUIRED_FEATURE_KEYS.difference(features.columns)
    missing_label = _REQUIRED_LABEL_COLUMNS.difference(labels.columns)
    if missing_feature:
        reasons.append(f"features missing columns: {sorted(missing_feature)}")
    if missing_label:
        reasons.append(f"labels missing columns: {sorted(missing_label)}")

    feature_columns: tuple[str, ...] = ()
    feature_rows = features.height
    label_rows = labels.height
    if not features.is_empty() and not missing_feature:
        feature_columns = tuple(
            column for column in features.columns
            if column not in _REQUIRED_FEATURE_KEYS
        )
        if not feature_columns:
            reasons.append("features contain no model feature columns")
        for column in feature_columns:
            if not features.schema[column].is_numeric():
                reasons.append(f"feature column {column} is not numeric")
                continue
            if features.filter(
                pl.col(column).is_not_null() & (~pl.col(column).cast(pl.Float64).is_finite())
            ).height:
                reasons.append(f"feature column {column} contains non-finite values")
        if features.select(pl.struct(["signal_date", "instrument"]).is_duplicated().any()).item():
            reasons.append("features contain duplicate signal_date/instrument keys")
        for value in features["signal_date"].cast(pl.String).to_list():
            try:
                _iso_date(value, "feature signal_date")
            except ValueError:
                reasons.append("features contain invalid signal_date")
                break
        if features.filter(
            (pl.col("signal_date").cast(pl.String) < start)
            | (pl.col("signal_date").cast(pl.String) >= cutoff)
            | (pl.col("signal_date").cast(pl.String) > end)
        ).height:
            reasons.append("feature signal dates escape the requested window")
    else:
        reasons.append("features must not be empty")

    if labels.is_empty():
        reasons.append("labels must not be empty")
    elif not missing_label:
        if labels.select(pl.struct(["signal_date", "instrument"]).is_duplicated().any()).item():
            reasons.append("labels contain duplicate signal_date/instrument keys")
        if labels.filter(
            pl.col("forward_return").is_null()
            | (~pl.col("forward_return").cast(pl.Float64).is_finite())
        ).height:
            reasons.append("labels contain null or non-finite forward returns")
        for column in ("signal_date", "label_end_date"):
            for value in labels[column].cast(pl.String).to_list():
                try:
                    _iso_date(value, f"label {column}")
                except ValueError:
                    reasons.append(f"labels contain invalid {column}")
                    break
        if labels.filter(
            (pl.col("signal_date").cast(pl.String) < start)
            | (pl.col("signal_date").cast(pl.String) >= cutoff)
            | (pl.col("label_end_date").cast(pl.String) <= pl.col("signal_date").cast(pl.String))
            | (pl.col("label_end_date").cast(pl.String) >= cutoff)
            | (pl.col("label_end_date").cast(pl.String) > end)
        ).height:
            reasons.append("labels include rows outside training window or not fully mature before cutoff")
        if not missing_feature and not features.is_empty():
            feature_keys = features.select("signal_date", "instrument").unique()
            label_keys = labels.select("signal_date", "instrument").unique()
            unmatched = label_keys.join(
                feature_keys, on=["signal_date", "instrument"], how="anti"
            )
            if unmatched.height:
                reasons.append("labels contain signal_date/instrument keys without matching features")

    if source_provenance.get("training_dataset_unlocked") is not False:
        reasons.append("source provenance must retain training_dataset_unlocked=false")

    provenance_bytes = json.dumps(
        source_provenance, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(provenance_bytes).hexdigest()
    report_id = str(acceptance_report.get("report_id", ""))
    feature_fingerprint = (
        _frame_fingerprint(features, keys=["signal_date", "instrument"])
        if not reasons and _REQUIRED_FEATURE_KEYS.issubset(features.columns)
        else ""
    )
    label_fingerprint = (
        _frame_fingerprint(labels, keys=["signal_date", "instrument"])
        if not reasons and _REQUIRED_LABEL_COLUMNS.issubset(labels.columns)
        else ""
    )
    return TrainingDatasetAdmission(
        admitted=not reasons,
        report_id=report_id,
        start_date=start,
        end_date=end,
        universe_profile=universe_profile,
        training_cutoff=cutoff,
        feature_rows=feature_rows,
        label_rows=label_rows,
        feature_columns=feature_columns,
        label_horizon_sessions=label_horizon_sessions,
        source_fingerprint=fingerprint,
        feature_fingerprint=feature_fingerprint,
        label_fingerprint=label_fingerprint,
        rejection_reasons=tuple(reasons),
    )
