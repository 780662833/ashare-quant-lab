"""Fail-closed point-in-time security metadata contract.

Provider-agnostic temporal observations with explicit effective dates,
availability timestamps and receipt-bound source identities. This module never
reads CNEquity or infers metadata from price bars or first-observed dates.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

SHANGHAI = ZoneInfo("Asia/Shanghai")

_REQUIRED_COLUMNS = {
    "instrument", "field", "classification_system", "value_numeric", "value_string", "value_date",
    "value_unit", "effective_from", "effective_to", "available_at", "evidence_class",
    "pit_grade", "quality", "is_exact", "source", "source_dataset",
    "source_revision_id", "source_contract_fingerprint", "source_receipt_id",
}
_VALUE_COLUMNS = ("value_numeric", "value_string", "value_date")

_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "listing_date": {
        "value_kind": "date",
        "value_unit": "date",
        "taxonomy": False,
        "evidence_class": "immutable_event",
        "pit_grade": "event_verified",
        "quality": "verified",
        "datasets": {"instruments"},
        "requires_open_interval": True,
        "value_date_matches_effective_from": True,
        "same_day_observation": False,
    },
    "industry_l1_code": {
        "value_kind": "string",
        "value_unit": "classification_code",
        "taxonomy": True,
        "classification_systems": {"eastmoney", "sw"},
        "datasets_by_classification_system": {
            "eastmoney": {"industry_members"},
            "sw": {"sw_industry_history"},
        },
        "evidence_class": "strict_pit",
        "pit_grade": "strict",
        "quality": "strict",
        "datasets": {"industry_members", "sw_industry_history"},
        "requires_open_interval": False,
        "value_date_matches_effective_from": False,
        "same_day_observation": False,
    },
    "industry_l2_code": {
        "value_kind": "string",
        "value_unit": "classification_code",
        "taxonomy": True,
        "classification_systems": {"eastmoney", "sw"},
        "datasets_by_classification_system": {
            "eastmoney": {"industry_members"},
            "sw": {"sw_industry_history"},
        },
        "evidence_class": "strict_pit",
        "pit_grade": "strict",
        "quality": "strict",
        "datasets": {"industry_members", "sw_industry_history"},
        "requires_open_interval": False,
        "value_date_matches_effective_from": False,
        "same_day_observation": False,
    },
    "industry_l3_code": {
        "value_kind": "string",
        "value_unit": "classification_code",
        "taxonomy": True,
        "classification_systems": {"eastmoney", "sw"},
        "datasets_by_classification_system": {
            "eastmoney": {"industry_members"},
            "sw": {"sw_industry_history"},
        },
        "evidence_class": "strict_pit",
        "pit_grade": "strict",
        "quality": "strict",
        "datasets": {"industry_members", "sw_industry_history"},
        "requires_open_interval": False,
        "value_date_matches_effective_from": False,
        "same_day_observation": False,
    },
    "market_cap_total": {
        "value_kind": "numeric",
        "value_unit": "CNY",
        "taxonomy": False,
        "evidence_class": "strict_pit",
        "pit_grade": "strict",
        "quality": "strict",
        "datasets": {"valuation_metrics", "derived_market_cap"},
        "requires_open_interval": False,
        "value_date_matches_effective_from": False,
        "same_day_observation": True,
    },
    "market_cap_float": {
        "value_kind": "numeric",
        "value_unit": "CNY",
        "taxonomy": False,
        "evidence_class": "strict_pit",
        "pit_grade": "strict",
        "quality": "strict",
        "datasets": {"valuation_metrics", "derived_market_cap"},
        "requires_open_interval": False,
        "value_date_matches_effective_from": False,
        "same_day_observation": True,
    },
}


def _iso_date(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical YYYY-MM-DD string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a canonical YYYY-MM-DD string")
    return value


def _aware_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(SHANGHAI)


def _validate_source_identities(
    source_identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(source_identities, Mapping) or not source_identities:
        raise ValueError("source_identities must be a non-empty receipt-derived mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for dataset, identity in source_identities.items():
        if not isinstance(dataset, str) or not dataset.strip():
            raise ValueError("source identity dataset names must be non-empty")
        if not isinstance(identity, Mapping):
            raise ValueError(f"source identity for {dataset} must be a mapping")
        revision_id = identity.get("revision_id")
        fingerprint = identity.get("contract_fingerprint")
        receipt_id = identity.get("receipt_id")
        evidence_classes = identity.get("validated_evidence_classes")
        if (
            not isinstance(revision_id, (str, int))
            or isinstance(revision_id, bool)
            or not str(revision_id).strip()
        ):
            raise ValueError(f"source identity for {dataset} has no revision_id")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ValueError(f"source identity for {dataset} has no contract_fingerprint")
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise ValueError(f"source identity for {dataset} has no receipt_id")
        if not isinstance(evidence_classes, (list, tuple, set)) or not evidence_classes:
            raise ValueError(f"source identity for {dataset} has no audited evidence capabilities")
        normalized[dataset] = {
            "revision_id": revision_id,
            "contract_fingerprint": fingerprint,
            "receipt_id": receipt_id,
            "validated_evidence_classes": set(evidence_classes),
        }
    return normalized


def validate_security_metadata(
    observations: pl.DataFrame,
    *,
    source_identities: Mapping[str, Mapping[str, Any]],
) -> pl.DataFrame:
    """Validate typed temporal observations against exact source receipt identities.

    Effective intervals are half-open: [effective_from, effective_to).
    Open-ended intervals are allowed only for immutable listing-date events;
    industry observations may be open-ended only when no later effective
    classification is known. Market-cap observations must be bounded.
    """
    if not isinstance(observations, pl.DataFrame):
        raise TypeError("observations must be a Polars DataFrame")
    if observations.is_empty():
        raise ValueError("security metadata observations must not be empty")
    missing = _REQUIRED_COLUMNS.difference(observations.columns)
    if missing:
        raise ValueError(f"security metadata missing columns: {sorted(missing)}")
    identities = _validate_source_identities(source_identities)

    frame = observations.select(sorted(_REQUIRED_COLUMNS)).with_columns(
        pl.col("instrument").cast(pl.String, strict=True),
        pl.col("field").cast(pl.String, strict=True),
        pl.col("classification_system").cast(pl.String, strict=True),
        pl.col("value_numeric").cast(pl.Float64, strict=True),
        pl.col("value_string").cast(pl.String, strict=True),
        pl.col("value_date").cast(pl.String, strict=True),
        pl.col("value_unit").cast(pl.String, strict=True),
        pl.col("effective_from").cast(pl.String, strict=True),
        pl.col("effective_to").cast(pl.String, strict=True),
        pl.col("available_at").cast(pl.String, strict=True),
        pl.col("evidence_class").cast(pl.String, strict=True),
        pl.col("pit_grade").cast(pl.String, strict=True),
        pl.col("quality").cast(pl.String, strict=True),
        pl.col("is_exact").cast(pl.Boolean, strict=True),
        pl.col("source").cast(pl.String, strict=True),
        pl.col("source_dataset").cast(pl.String, strict=True),
        pl.col("source_revision_id").cast(pl.String, strict=True),
        pl.col("source_contract_fingerprint").cast(pl.String, strict=True),
        pl.col("source_receipt_id").cast(pl.String, strict=True),
    )

    for column in (
        "instrument", "field", "effective_from", "available_at", "value_unit",
        "evidence_class", "pit_grade", "quality", "source",
        "source_dataset", "source_revision_id",
        "source_contract_fingerprint", "source_receipt_id",
    ):
        if frame[column].null_count() or frame.filter(
            pl.col(column).str.strip_chars() == ""
        ).height:
            raise ValueError(f"security metadata contains null or empty {column}")

    normalized_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in frame.to_dicts():
        instrument = str(row["instrument"]).strip()
        field = str(row["field"]).strip()
        if not instrument:
            raise ValueError("security metadata instrument must be non-empty")
        spec = _FIELD_SPECS.get(field)
        if spec is None:
            raise ValueError(f"unsupported security metadata field: {field}")

        effective_from = _iso_date(row["effective_from"], "effective_from")
        effective_to = _iso_date(row["effective_to"], "effective_to", nullable=True)
        value_date = _iso_date(row["value_date"], "value_date", nullable=True)
        available_at = _aware_timestamp(row["available_at"], "available_at")
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be later than effective_from")
        if spec["requires_open_interval"] and effective_to is not None:
            raise ValueError(f"{field} must use an open-ended immutable-event interval")
        if spec["value_date_matches_effective_from"] and effective_from != value_date:
            raise ValueError(f"{field} effective_from must equal its value date")
        if row["value_unit"] != spec["value_unit"]:
            raise ValueError(f"{field} must use value_unit={spec['value_unit']}")
        classification_system = row["classification_system"]
        if spec["taxonomy"]:
            if classification_system not in spec["classification_systems"]:
                raise ValueError(f"{field} requires a supported classification_system")
        elif classification_system is not None:
            raise ValueError(f"{field} must not set classification_system")

        populated = [name for name in _VALUE_COLUMNS if row[name] is not None]
        expected_column = {
            "numeric": "value_numeric",
            "string": "value_string",
            "date": "value_date",
        }[spec["value_kind"]]
        if set(populated) != {expected_column}:
            raise ValueError(
                f"{field} must populate only {expected_column}; got {populated}"
            )
        if expected_column == "value_numeric":
            value = float(row["value_numeric"])
            if not isfinite(value) or value < 0:
                raise ValueError(f"{field} must be finite and non-negative")
        elif expected_column == "value_string":
            if not str(row["value_string"]).strip():
                raise ValueError(f"{field} must be a non-empty code")
        elif value_date is None:
            raise ValueError(f"{field} requires a value_date")

        if row["evidence_class"] != spec["evidence_class"]:
            raise ValueError(f"{field} has an invalid evidence_class")
        if row["pit_grade"] != spec["pit_grade"] or row["quality"] != spec["quality"]:
            raise ValueError(f"{field} does not meet its required source quality")
        if row["is_exact"] is not True:
            raise ValueError(f"{field} must carry is_exact=true")
        dataset = str(row["source_dataset"])
        if spec["taxonomy"]:
            permitted_datasets = spec["datasets_by_classification_system"][classification_system]
        else:
            permitted_datasets = spec["datasets"]
        if dataset not in permitted_datasets:
            raise ValueError(
                f"{field} from {classification_system} is not permitted from {dataset}"
            )
        identity = identities.get(dataset)
        if identity is None:
            raise ValueError(f"source receipt identity is missing for dataset {dataset}")
        if spec["evidence_class"] not in identity["validated_evidence_classes"]:
            raise ValueError(
                f"source identity for {dataset} does not certify {spec['evidence_class']}"
            )
        if (
            str(row["source_revision_id"]) != str(identity["revision_id"])
            or row["source_contract_fingerprint"] != identity["contract_fingerprint"]
            or row["source_receipt_id"] != identity["receipt_id"]
        ):
            raise ValueError(f"{field} row does not match its source receipt identity")

        duplicate_key = (
            instrument, field, classification_system, effective_from, available_at.isoformat()
        )
        if duplicate_key in seen:
            raise ValueError("duplicate security metadata instrument/field/effective/available key")
        seen.add(duplicate_key)

        normalized_rows.append({
            **row,
            "instrument": instrument,
            "field": field,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "value_date": value_date,
            "available_at": available_at.isoformat(),
        })

    for row in normalized_rows:
        spec = _FIELD_SPECS[row["field"]]
        if spec["value_kind"] == "numeric" and row["effective_to"] is None:
            raise ValueError(f"{row['field']} requires a bounded effective interval")

    return pl.DataFrame(normalized_rows, schema=frame.schema).sort(
        ["instrument", "field", "effective_from", "available_at"]
    )


def select_security_metadata_as_of(
    observations: pl.DataFrame,
    *,
    source_identities: Mapping[str, Mapping[str, Any]],
    instruments: Sequence[str],
    fields: Sequence[str],
    signal_date: str,
    classification_system: str | None = None,
    decision_at: str,
) -> pl.DataFrame:
    """Select metadata effective and knowable at one Shanghai signal timestamp.

    For each instrument/field/effective date, the latest revision available by
    decision_at wins. The effective interval must contain signal_date. No
    future publication, future effective event, or forward-fill is allowed.
    """
    signal = _iso_date(signal_date, "signal_date")
    if not instruments or len(set(instruments)) != len(instruments):
        raise ValueError("instruments must be non-empty and unique")
    if not fields or len(set(fields)) != len(fields):
        raise ValueError("fields must be non-empty and unique")
    unknown_fields = sorted(set(fields).difference(_FIELD_SPECS))
    if unknown_fields:
        raise ValueError(f"unsupported security metadata fields: {unknown_fields}")
    taxonomy_fields = [field for field in fields if _FIELD_SPECS[field]["taxonomy"]]
    if taxonomy_fields:
        allowed_systems = set.intersection(*[
            set(_FIELD_SPECS[field]["classification_systems"])
            for field in taxonomy_fields
        ])
        if classification_system not in allowed_systems:
            raise ValueError(
                "industry metadata selection requires one explicit classification_system"
            )
    elif classification_system is not None:
        raise ValueError("classification_system is only valid for industry metadata fields")
    cutoff = _aware_timestamp(decision_at, "decision_at")
    if cutoff.date().isoformat() != signal:
        raise ValueError("decision_at must fall on the supplied Shanghai signal_date")

    frame = validate_security_metadata(
        observations, source_identities=source_identities
    )
    instrument_set = {str(item) for item in instruments}
    field_set = set(fields)
    rows = [
        row for row in frame.to_dicts()
        if row["instrument"] in instrument_set
        and row["field"] in field_set
        and (
            not _FIELD_SPECS[row["field"]]["taxonomy"]
            or row["classification_system"] == classification_system
        )
        and _aware_timestamp(row["available_at"], "available_at") <= cutoff
    ]

    latest_by_start: dict[tuple[str, str, str | None, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            row["instrument"], row["field"], row["classification_system"],
            row["effective_from"],
        )
        previous = latest_by_start.get(key)
        if previous is None or row["available_at"] > previous["available_at"]:
            latest_by_start[key] = row

    by_instrument_field: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for row in latest_by_start.values():
        by_instrument_field.setdefault(
            (row["instrument"], row["field"], row["classification_system"]), []
        ).append(row)

    selected: list[dict[str, Any]] = []
    for key, candidates in by_instrument_field.items():
        ordered = sorted(candidates, key=lambda row: row["effective_from"])
        previous: dict[str, Any] | None = None
        for current in ordered:
            if previous is not None:
                previous_end = previous["effective_to"]
                if previous_end is not None and previous_end > current["effective_from"]:
                    raise ValueError(
                        f"overlapping effective intervals for {key[0]} / {key[1]}"
                    )
            previous = current
        applicable = [
            row for row in ordered
            if row["effective_from"] <= signal
            and (row["effective_to"] is None or signal < row["effective_to"])
            and (
                not _FIELD_SPECS[row["field"]]["same_day_observation"]
                or row["effective_from"] == signal
            )
        ]
        if applicable:
            # An open-ended prior interval is implicitly superseded by the
            # next known effective_from. Explicit overlapping bounded
            # intervals were rejected above.
            selected.append(max(applicable, key=lambda row: row["effective_from"]))

    output_schema = dict(frame.schema)
    output_schema["signal_date"] = pl.String
    output_schema["decision_at"] = pl.String
    if selected:
        stamped = [
            {**row, "signal_date": signal, "decision_at": cutoff.isoformat()}
            for row in selected
        ]
        return pl.DataFrame(stamped, schema=output_schema).sort(["instrument", "field"])
    return frame.head(0).with_columns(
        pl.lit(signal).alias("signal_date"),
        pl.lit(cutoff.isoformat()).alias("decision_at"),
    )


def audit_security_metadata_coverage(
    selected: pl.DataFrame,
    *,
    instruments: Sequence[str],
    fields: Sequence[str],
    classification_system: str | None = None,
    signal_date: str | None = None,
    decision_at: str | None = None,
) -> dict[str, Any]:
    """Require one selected value for every requested instrument/field pair."""
    if not isinstance(selected, pl.DataFrame):
        raise TypeError("selected must be a Polars DataFrame")
    if not instruments or len(set(instruments)) != len(instruments):
        raise ValueError("instruments must be non-empty and unique")
    if not fields or len(set(fields)) != len(fields):
        raise ValueError("fields must be non-empty and unique")
    unknown_fields = sorted(set(fields).difference(_FIELD_SPECS))
    if unknown_fields:
        raise ValueError(f"unsupported security metadata fields: {unknown_fields}")
    required_columns = {
        "instrument", "field", "classification_system", "signal_date", "decision_at"
    }
    if not required_columns.issubset(selected.columns):
        raise ValueError(
            "selected metadata must contain instrument, field, taxonomy and selection timestamp"
        )
    taxonomy_fields = [field for field in fields if field in _FIELD_SPECS and _FIELD_SPECS[field]["taxonomy"]]
    if taxonomy_fields:
        allowed_systems = set.intersection(*[
            set(_FIELD_SPECS[field]["classification_systems"])
            for field in taxonomy_fields
        ])
        if classification_system not in allowed_systems:
            raise ValueError("coverage audit requires an explicit industry classification_system")
    elif classification_system is not None:
        raise ValueError("classification_system is only valid for industry metadata fields")

    if selected.height:
        signal_dates = selected["signal_date"].unique().to_list()
        decision_times = selected["decision_at"].unique().to_list()
        if len(signal_dates) != 1 or len(decision_times) != 1:
            raise ValueError("coverage audit accepts one signal-date metadata snapshot at a time")
        observed_signal = _iso_date(signal_dates[0], "selected.signal_date")
        observed_decision = _aware_timestamp(decision_times[0], "selected.decision_at")
        if observed_decision.date().isoformat() != observed_signal:
            raise ValueError("selected metadata decision_at does not match signal_date")
        if signal_date is not None and _iso_date(signal_date, "signal_date") != observed_signal:
            raise ValueError("selected metadata signal_date differs from audit signal_date")
        if decision_at is not None:
            requested_decision = _aware_timestamp(decision_at, "decision_at")
            if requested_decision != observed_decision:
                raise ValueError("selected metadata decision_at differs from audit decision_at")
    else:
        if signal_date is None or decision_at is None:
            raise ValueError("empty coverage audit requires explicit signal_date and decision_at")
        observed_signal = _iso_date(signal_date, "signal_date")
        observed_decision = _aware_timestamp(decision_at, "decision_at")
        if observed_decision.date().isoformat() != observed_signal:
            raise ValueError("decision_at must fall on signal_date")

    if taxonomy_fields:
        taxonomy_rows = selected.filter(pl.col("field").is_in(taxonomy_fields))
        if taxonomy_rows.height and taxonomy_rows.filter(
            pl.col("classification_system") != classification_system
        ).height:
            raise ValueError("coverage audit cannot mix industry classification systems")
    if selected.filter(
        pl.col("field").is_in([field for field in fields if field not in taxonomy_fields])
        & pl.col("classification_system").is_not_null()
    ).height:
        raise ValueError("non-taxonomy metadata rows must have null classification_system")
    if selected.filter(~pl.col("field").is_in(list(fields))).height:
        raise ValueError("selected metadata contains fields outside the coverage request")

    required = {
        (str(instrument), str(field))
        for instrument in instruments
        for field in fields
    }
    observed_rows = selected.select("instrument", "field").to_dicts()
    observed = [
        (str(row["instrument"]), str(row["field"]))
        for row in observed_rows
    ]
    if len(observed) != len(set(observed)):
        raise ValueError("selected metadata contains duplicate instrument/field pairs")
    observed_set = set(observed)
    missing = sorted(required - observed_set)
    unexpected = sorted(observed_set - required)
    expected_count = len(required)
    observed_count = len(observed_set)
    return {
        "status": "PASS" if not missing and not unexpected else "FAIL",
        "expected_pairs": expected_count,
        "observed_pairs": observed_count,
        "missing_pairs": len(missing),
        "unexpected_pairs": len(unexpected),
        "coverage_rate": observed_count / expected_count if expected_count else 0.0,
        "missing_examples": [list(item) for item in missing[:20]],
        "unexpected_examples": [list(item) for item in unexpected[:20]],
        "fields": list(fields),
        "instrument_count": len(instruments),
        "signal_date": observed_signal,
        "decision_at": observed_decision.isoformat(),
        "classification_system": classification_system,
        "selection_policy": (
            "effective interval contains signal_date AND available_at <= "
            "Shanghai decision_at; no forward-fill or proxy imputation"
        ),
    }
