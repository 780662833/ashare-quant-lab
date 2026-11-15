import polars as pl
import pytest

from ashare_quant.research.listing_event_evidence import (
    validate_listing_event_evidence,
)
from ashare_quant.research.security_metadata import build_security_metadata_source_identity


def identity(dataset):
    revision_id = f"{dataset}-r1"
    fingerprint = f"{dataset}-contract"
    receipt_id = f"{dataset}-receipt"
    receipt = {
        "receipt_id": receipt_id,
        "replayable": True,
        "dependencies": {
            dataset: {
                "revision_id": revision_id,
                "contract_fingerprint": fingerprint,
            },
        },
    }
    audit = {
        "audit_id": f"{dataset}-immutable-event-audit-v1",
        "dataset": dataset,
        "evidence_class": "immutable_event",
        "status": "PASS",
        "scope": "field_semantics",
        "method": "synthetic-exchange-evidence-test",
        "audited_fields": ["listing_date"],
        "audited_at": "2026-01-01T00:00:00+08:00",
        "receipt_id": receipt_id,
        "revision_id": revision_id,
        "contract_fingerprint": fingerprint,
    }
    return build_security_metadata_source_identity(
        dataset,
        receipt=receipt,
        revision_identity={
            "revision_id": revision_id,
            "contract_fingerprint": fingerprint,
        },
        capability_audits=[audit],
    )


IDENTITIES = {
    dataset: identity(dataset)
    for dataset in (
        "sse_listing_events",
        "szse_listing_events",
        "bse_listing_events",
    )
}


def event_row(exchange="SSE", **overrides):
    dataset = {
        "SSE": "sse_listing_events",
        "SZSE": "szse_listing_events",
        "BSE": "bse_listing_events",
    }[exchange]
    receipt = IDENTITIES[dataset]["receipt_id"]
    revision = IDENTITIES[dataset]["revision_id"]
    fingerprint = IDENTITIES[dataset]["contract_fingerprint"]
    row = {
        "instrument": {
            "SSE": "600000.SH",
            "SZSE": "000001.SZ",
            "BSE": "920001.BJ",
        }[exchange],
        "exchange": exchange,
        "event_type": "initial_listing",
        "listing_date": "2024-01-05",
        "source_dataset": dataset,
        "document_kind": "listing_notice",
        "document_id": f"{exchange}-NOTICE-20240102",
        "document_url": {
            "SSE": "https://www.sse.com.cn/disclosure/announcement/listing/stock/test.html",
            "SZSE": "https://www.szse.cn/disclosure/notice/test.html",
            "BSE": "https://www.bse.cn/disclosure/notice/test.html",
        }[exchange],
        "document_sha256": "a" * 64,
        "record_locator": "Page 2, table 1, row 3",
        "publication_date": "2024-01-02",
        "publication_precision": "date",
        "published_at": "2024-01-02T23:59:59+08:00",
        "available_at": "2024-01-02T23:59:59+08:00",
        "document_date": "2024-01-01",
        "captured_at": "2024-01-03T09:00:00+08:00",
        "is_bse_select_layer_transfer": False,
        "source_revision_id": revision,
        "source_contract_fingerprint": fingerprint,
        "source_receipt_id": receipt,
    }
    row.update(overrides)
    return row


def frame(*rows):
    return pl.DataFrame(rows, schema_overrides={"document_date": pl.String})


def validate(*rows):
    datasets = {row["source_dataset"] for row in rows}
    return validate_listing_event_evidence(
        frame(*rows),
        source_identities={dataset: IDENTITIES[dataset] for dataset in datasets},
    )


def test_accepts_exchange_official_document_with_conservative_date_precision():
    result = validate(event_row())
    assert result.height == 1
    row = result.row(0, named=True)
    assert row["publication_date"] == "2024-01-02"
    assert row["published_at"] == "2024-01-02T23:59:59+08:00"
    assert row["available_at"] == row["published_at"]
    assert row["document_date"] == "2024-01-01"
    assert row["document_sha256"] == "a" * 64


@pytest.mark.parametrize("exchange", ["SSE", "SZSE", "BSE"])
def test_accepts_exchange_specific_official_domains_and_symbol_suffixes(exchange):
    result = validate(event_row(exchange))
    assert result["exchange"].item() == exchange


def test_accepts_timestamp_precision_and_preserves_public_release_time():
    result = validate(event_row(
        publication_precision="timestamp",
        published_at="2024-01-02T10:30:00+08:00",
        available_at="2024-01-02T10:30:00+08:00",
        captured_at="2024-01-02T10:31:00+08:00",
    ))
    assert result["available_at"].item() == "2024-01-02T10:30:00+08:00"


def test_rejects_date_precision_without_end_of_day_cutoff():
    with pytest.raises(ValueError, match="date-precision publication"):
        validate(event_row(
            published_at="2024-01-02T10:30:00+08:00",
            available_at="2024-01-02T10:30:00+08:00",
        ))


def test_rejects_available_at_that_differs_from_documented_public_release():
    with pytest.raises(ValueError, match="available_at must equal"):
        validate(event_row(
            publication_precision="timestamp",
            published_at="2024-01-02T10:30:00+08:00",
            available_at="2024-01-02T10:31:00+08:00",
        ))


def test_rejects_non_exchange_official_or_lookalike_domains():
    with pytest.raises(ValueError, match="HTTPS URL on sse.com.cn"):
        validate(event_row(
            document_url="https://sse.com.cn.evil.example/listing.pdf",
        ))


def test_rejects_non_https_document_url():
    with pytest.raises(ValueError, match="HTTPS URL on sse.com.cn"):
        validate(event_row(
            document_url="http://www.sse.com.cn/listing.pdf",
        ))


def test_rejects_bad_document_digest():
    with pytest.raises(ValueError, match="64-character SHA-256"):
        validate(event_row(document_sha256="not-a-digest"))


def test_normalizes_sha256_hex_to_lowercase():
    result = validate(event_row(document_sha256="A" * 64))
    assert result["document_sha256"].item() == "a" * 64


def test_rejects_missing_record_locator():
    with pytest.raises(ValueError, match="record_locator"):
        validate(event_row(record_locator="  "))


def test_rejects_publication_timestamp_without_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        validate(event_row(
            publication_precision="timestamp",
            published_at="2024-01-02T10:30:00",
            available_at="2024-01-02T10:30:00",
        ))


def test_rejects_capture_before_publication():
    with pytest.raises(ValueError, match="cannot precede"):
        validate(event_row(captured_at="2024-01-02T09:00:00+08:00"))


def test_rejects_bse_select_layer_transfer_listing_age_ambiguity():
    with pytest.raises(ValueError, match="remain unqualified"):
        validate(event_row(
            "BSE",
            is_bse_select_layer_transfer=True,
        ))


def test_rejects_select_layer_transfer_flag_on_non_bse_record():
    with pytest.raises(ValueError, match="only valid for BSE"):
        validate(event_row(is_bse_select_layer_transfer=True))


def test_rejects_szse_monthly_report_as_wrong_exchange_document_kind():
    with pytest.raises(ValueError, match="only accepted for SSE"):
        validate(event_row(
            "SZSE",
            document_kind="monthly_listing_report",
        ))


def test_rejects_wrong_exchange_qualified_instrument():
    with pytest.raises(ValueError, match="six-digit exchange-qualified"):
        validate(event_row(instrument="600000.SZ"))


def test_rejects_wrong_dataset_namespace_for_exchange():
    with pytest.raises(ValueError, match="must use source_dataset"):
        validate(event_row(source_dataset="bse_listing_events"))


def test_rejects_receipt_revision_or_fingerprint_drift():
    with pytest.raises(ValueError, match="does not match its source receipt identity"):
        validate(event_row(source_receipt_id="different-receipt"))


def test_rejects_duplicate_source_event_for_one_instrument():
    row = event_row()
    with pytest.raises(ValueError, match="duplicate listing-event evidence"):
        validate(row, row)


def test_rejects_unregistered_document_kind():
    with pytest.raises(ValueError, match="unsupported listing-event document_kind"):
        validate(event_row(document_kind="current_security_master"))


def test_rejects_non_initial_listing_event():
    with pytest.raises(ValueError, match="only accepts event_type=initial_listing"):
        validate(event_row(event_type="delisting"))


def test_rejects_empty_evidence_frame():
    with pytest.raises(ValueError, match="must not be empty"):
        validate_listing_event_evidence(
            pl.DataFrame(),
            source_identities=IDENTITIES,
        )
