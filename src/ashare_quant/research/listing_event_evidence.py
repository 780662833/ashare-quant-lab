"""Fail-closed evidence envelope for immutable A-share listing events.

This module validates source-document provenance only. It does not fetch
exchange pages, authenticate source documents, or establish historical
universe completeness.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlsplit

import polars as pl

from ashare_quant.research.security_metadata import (
    SHANGHAI,
    _aware_timestamp,
    _iso_date,
    validate_security_metadata,
)

_REQUIRED_COLUMNS = {
    "instrument",
    "exchange",
    "event_type",
    "listing_date",
    "source_dataset",
    "document_kind",
    "document_id",
    "document_url",
    "document_sha256",
    "record_locator",
    "publication_date",
    "publication_precision",
    "published_at",
    "available_at",
    "document_date",
    "captured_at",
    "is_bse_select_layer_transfer",
    "source_revision_id",
    "source_contract_fingerprint",
    "source_receipt_id",
}
_EXCHANGE_SPECS = {
    "SSE": {
        "suffix": ".SH",
        "dataset": "sse_listing_events",
        "domain": "sse.com.cn",
    },
    "SZSE": {
        "suffix": ".SZ",
        "dataset": "szse_listing_events",
        "domain": "szse.cn",
    },
    "BSE": {
        "suffix": ".BJ",
        "dataset": "bse_listing_events",
        "domain": "bse.cn",
    },
}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_INSTRUMENT = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"listing-event evidence requires non-empty {field}")
    return value.strip()


def _official_document_url(value: str, *, exchange: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("document_url must be a valid official HTTPS URL") from exc
    expected_domain = _EXCHANGE_SPECS[exchange]["domain"]
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or not (host == expected_domain or host.endswith("." + expected_domain))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.port not in (None, 443)
        or not parsed.path
    ):
        raise ValueError(
            f"{exchange} document_url must be an HTTPS URL on {expected_domain}"
        )
    return value


def _document_timestamp(row: Mapping[str, Any], field: str) -> datetime:
    return _aware_timestamp(row.get(field), f"listing_event.{field}")


def validate_listing_event_evidence(
    evidence: pl.DataFrame,
    *,
    source_identities: Mapping[str, Mapping[str, Any]],
) -> pl.DataFrame:
    """Validate official-document evidence for initial listing-date events.

    Date-only publication records receive a conservative 23:59:59
    Asia/Shanghai availability cutoff. Document/signature dates are retained
    separately and are never substituted for the public release timestamp.

    BSE Select-Layer transfers fail closed because their legal listing-age
    start may precede the BSE trading/code-migration date.
    """
    if not isinstance(evidence, pl.DataFrame):
        raise TypeError("evidence must be a Polars DataFrame")
    if evidence.is_empty():
        raise ValueError("listing-event evidence must not be empty")
    missing = sorted(_REQUIRED_COLUMNS.difference(evidence.columns))
    if missing:
        raise ValueError(f"listing-event evidence missing columns: {missing}")

    normalized: list[dict[str, Any]] = []
    metadata_observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in evidence.select(sorted(_REQUIRED_COLUMNS)).to_dicts():
        instrument = _required_text(row, "instrument").upper()
        exchange = _required_text(row, "exchange").upper()
        if exchange not in _EXCHANGE_SPECS:
            raise ValueError(f"unsupported listing-event exchange: {exchange}")
        exchange_spec = _EXCHANGE_SPECS[exchange]
        if not _INSTRUMENT.fullmatch(instrument) or not instrument.endswith(
            exchange_spec["suffix"]
        ):
            raise ValueError(
                f"{exchange} instrument must be a six-digit exchange-qualified "
                f"symbol ending {exchange_spec['suffix']}"
            )

        event_type = _required_text(row, "event_type")
        if event_type != "initial_listing":
            raise ValueError("listing-event evidence only accepts event_type=initial_listing")
        transfer = row.get("is_bse_select_layer_transfer")
        if not isinstance(transfer, bool):
            raise ValueError("is_bse_select_layer_transfer must be an explicit boolean")
        if exchange == "BSE" and transfer:
            raise ValueError(
                "BSE Select-Layer transfers remain unqualified for listing-age start"
            )
        if exchange != "BSE" and transfer:
            raise ValueError("Select-Layer transfer flag is only valid for BSE records")

        listing_date = _iso_date(row.get("listing_date"), "listing_date")
        dataset = _required_text(row, "source_dataset")
        if dataset != exchange_spec["dataset"]:
            raise ValueError(
                f"{exchange} listing evidence must use source_dataset="
                f"{exchange_spec['dataset']}"
            )

        document_kind = _required_text(row, "document_kind")
        if document_kind not in {"listing_notice", "monthly_listing_report"}:
            raise ValueError(f"unsupported listing-event document_kind: {document_kind}")
        if document_kind == "monthly_listing_report" and exchange != "SSE":
            raise ValueError("monthly_listing_report is only accepted for SSE evidence")

        document_id = _required_text(row, "document_id")
        document_url = _official_document_url(
            _required_text(row, "document_url"), exchange=exchange
        )
        document_sha256 = _required_text(row, "document_sha256").lower()
        if not _SHA256.fullmatch(document_sha256):
            raise ValueError("document_sha256 must be a 64-character SHA-256 hex digest")
        record_locator = _required_text(row, "record_locator")

        publication_date = _iso_date(row.get("publication_date"), "publication_date")
        precision = _required_text(row, "publication_precision")
        if precision not in {"date", "timestamp"}:
            raise ValueError("publication_precision must be date or timestamp")
        published_at = _document_timestamp(row, "published_at")
        available_at = _document_timestamp(row, "available_at")
        captured_at = _document_timestamp(row, "captured_at")
        if published_at.date().isoformat() != publication_date:
            raise ValueError("published_at local date must equal publication_date")
        if available_at != published_at:
            raise ValueError("available_at must equal the documented public release time")
        if captured_at < published_at:
            raise ValueError("captured_at cannot precede the public release time")

        if precision == "date":
            conservative_release = datetime.combine(
                date.fromisoformat(publication_date),
                time(23, 59, 59),
                tzinfo=SHANGHAI,
            )
            if published_at != conservative_release:
                raise ValueError(
                    "date-precision publication must use 23:59:59 Asia/Shanghai"
                )

        document_date_value = row.get("document_date")
        document_date = (
            _iso_date(document_date_value, "document_date", nullable=True)
            if document_date_value is not None
            else None
        )

        revision_id = _required_text(row, "source_revision_id")
        fingerprint = _required_text(row, "source_contract_fingerprint")
        receipt_id = _required_text(row, "source_receipt_id")
        key = (dataset, instrument)
        if key in seen:
            raise ValueError(
                f"duplicate listing-event evidence for {instrument} from {dataset}"
            )
        seen.add(key)

        normalized_row = {
            **row,
            "instrument": instrument,
            "exchange": exchange,
            "event_type": event_type,
            "listing_date": listing_date,
            "source_dataset": dataset,
            "document_kind": document_kind,
            "document_id": document_id,
            "document_url": document_url,
            "document_sha256": document_sha256,
            "record_locator": record_locator,
            "publication_date": publication_date,
            "publication_precision": precision,
            "published_at": published_at.isoformat(),
            "available_at": available_at.isoformat(),
            "document_date": document_date,
            "captured_at": captured_at.isoformat(),
            "is_bse_select_layer_transfer": transfer,
            "source_revision_id": revision_id,
            "source_contract_fingerprint": fingerprint,
            "source_receipt_id": receipt_id,
        }
        normalized.append(normalized_row)
        metadata_observations.append({
            "instrument": instrument,
            "field": "listing_date",
            "classification_system": None,
            "value_numeric": None,
            "value_string": None,
            "value_date": listing_date,
            "value_unit": "date",
            "effective_from": listing_date,
            "effective_to": None,
            "available_at": available_at.isoformat(),
            "evidence_class": "immutable_event",
            "pit_grade": "event_verified",
            "quality": "verified",
            "is_exact": True,
            "source": exchange.lower(),
            "source_dataset": dataset,
            "source_revision_id": revision_id,
            "source_contract_fingerprint": fingerprint,
            "source_receipt_id": receipt_id,
        })

    metadata_frame = pl.DataFrame(
        metadata_observations,
        schema_overrides={
            "classification_system": pl.String,
            "value_numeric": pl.Float64,
            "value_string": pl.String,
            "value_date": pl.String,
            "effective_to": pl.String,
        },
    )
    # Reuse the canonical security-metadata contract for receipt, capability,
    # revision, and immutable-event checks instead of duplicating those rules.
    validate_security_metadata(
        metadata_frame,
        source_identities=source_identities,
    )
    return pl.DataFrame(normalized).sort(["exchange", "instrument"])


def audit_listing_event_signal_coverage(
    evidence: pl.DataFrame,
    *,
    instruments: Sequence[str],
    source_identities: Mapping[str, Mapping[str, Any]],
    signal_date: str,
    decision_at: str,
) -> dict[str, Any]:
    """Audit listing-event evidence known for one historical signal date.

    instruments must be the exact exchange-qualified membership snapshot
    supplied by the caller's independently accepted point-in-time universe.
    This function validates event provenance and publication timing; it does
    not establish that the supplied universe itself is complete or PIT.

    Date-only public releases are conservatively represented by the validator
    as 23:59:59 Asia/Shanghai, so they cannot be used for an earlier same-day
    decision.
    """
    if isinstance(instruments, (str, bytes)) or not isinstance(instruments, Sequence):
        raise TypeError("instruments must be a sequence of exchange-qualified symbols")
    if not instruments:
        raise ValueError("instruments must contain at least one expected symbol")

    normalized_expected: list[str] = []
    for value in instruments:
        if not isinstance(value, str):
            raise ValueError("expected instruments must be strings")
        symbol = value.strip().upper()
        if not _INSTRUMENT.fullmatch(symbol):
            raise ValueError(
                "expected instruments must use six digits and .SH, .SZ or .BJ suffixes"
            )
        normalized_expected.append(symbol)
    if len(normalized_expected) != len(set(normalized_expected)):
        raise ValueError("expected instruments must be unique")

    normalized_signal_date = _iso_date(signal_date, "signal_date")
    cutoff = _aware_timestamp(decision_at, "decision_at")
    if cutoff.date().isoformat() != normalized_signal_date:
        raise ValueError("decision_at must fall on signal_date")

    validated = validate_listing_event_evidence(
        evidence,
        source_identities=source_identities,
    )
    event_by_symbol = {
        str(row["instrument"]): row
        for row in validated.to_dicts()
    }
    expected = set(normalized_expected)
    missing_evidence: list[str] = []
    not_yet_public: list[str] = []
    future_listing_events: list[str] = []
    available_by_cutoff: set[str] = set()

    for symbol in sorted(expected):
        event = event_by_symbol.get(symbol)
        if event is None:
            missing_evidence.append(symbol)
            continue
        if str(event["listing_date"]) > normalized_signal_date:
            future_listing_events.append(symbol)
            continue
        published = _document_timestamp(event, "available_at")
        if published > cutoff:
            not_yet_public.append(symbol)
            continue
        available_by_cutoff.add(symbol)

    by_exchange: list[dict[str, Any]] = []
    for exchange, suffix in (("SSE", ".SH"), ("SZSE", ".SZ"), ("BSE", ".BJ")):
        exchange_expected = sorted(symbol for symbol in expected if symbol.endswith(suffix))
        exchange_covered = sorted(available_by_cutoff.intersection(exchange_expected))
        exchange_missing = sorted(set(exchange_expected).intersection(missing_evidence))
        exchange_late = sorted(set(exchange_expected).intersection(not_yet_public))
        exchange_future = sorted(
            set(exchange_expected).intersection(future_listing_events)
        )
        by_exchange.append({
            "exchange": exchange,
            "expected": len(exchange_expected),
            "available_by_decision": len(exchange_covered),
            "missing_evidence": len(exchange_missing),
            "not_yet_public": len(exchange_late),
            "listing_after_signal_date": len(exchange_future),
            "missing_symbols": exchange_missing,
            "not_yet_public_symbols": exchange_late,
            "future_listing_symbols": exchange_future,
        })

    unexpected_evidence = sorted(set(event_by_symbol).difference(expected))
    passed = not (
        missing_evidence or not_yet_public or future_listing_events
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "signal_date": normalized_signal_date,
        "decision_at": cutoff.isoformat(),
        "expected_instruments": len(expected),
        "validated_evidence_rows": validated.height,
        "available_by_decision": len(available_by_cutoff),
        "missing_evidence_count": len(missing_evidence),
        "not_yet_public_count": len(not_yet_public),
        "listing_after_signal_date_count": len(future_listing_events),
        "missing_evidence_symbols": missing_evidence,
        "not_yet_public_symbols": not_yet_public,
        "listing_after_signal_date_symbols": future_listing_events,
        "additional_validated_evidence_count": len(unexpected_evidence),
        "additional_validated_evidence_symbols": unexpected_evidence,
        "by_exchange": by_exchange,
        "scope_note": (
            "The expected membership snapshot is caller-supplied. PASS means "
            "the supplied members have validated listing-event evidence public "
            "by this decision cutoff; it does not prove historical-universe "
            "completeness, delisting history, or training/backtest admission."
        ),
        "historical_universe_completeness_proven": False,
        "training_dataset_unlocked": False,
        "backtest_admitted": False,
    }
