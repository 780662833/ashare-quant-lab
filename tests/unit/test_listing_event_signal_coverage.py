from __future__ import annotations

import polars as pl
import pytest

from ashare_quant.research.listing_event_evidence import (
    audit_listing_event_signal_coverage,
)
from ashare_quant.research.security_metadata import (
    build_security_metadata_source_identity,
)


def _source_identity(dataset: str) -> dict[str, object]:
    revision_id = f"revision-{dataset}"
    fingerprint = "b" * 64
    receipt_id = f"receipt-{dataset}"
    receipt = {
        "replayable": True,
        "receipt_id": receipt_id,
        "dependencies": {
            dataset: {
                "revision_id": revision_id,
                "contract_fingerprint": fingerprint,
            }
        },
    }
    audit = {
        "audit_id": f"audit-{dataset}",
        "dataset": dataset,
        "status": "PASS",
        "scope": "field_semantics",
        "method": "independent_fixture_review",
        "evidence_class": "immutable_event",
        "receipt_id": receipt_id,
        "revision_id": revision_id,
        "contract_fingerprint": fingerprint,
        "audited_at": "2026-11-04T10:00:00+08:00",
        "audited_fields": ["listing_date"],
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


def _event_row(
    *,
    instrument: str = "600000.SH",
    exchange: str = "SSE",
    listing_date: str = "2026-10-20",
    publication_date: str = "2026-11-02",
    publication_precision: str = "timestamp",
    published_at: str = "2026-11-02T11:00:00+08:00",
) -> dict[str, object]:
    dataset = {
        "SSE": "sse_listing_events",
        "SZSE": "szse_listing_events",
        "BSE": "bse_listing_events",
    }[exchange]
    return {
        "instrument": instrument,
        "exchange": exchange,
        "event_type": "initial_listing",
        "listing_date": listing_date,
        "source_dataset": dataset,
        "document_kind": "listing_notice",
        "document_id": f"notice-{instrument}",
        "document_url": {
            "SSE": "https://www.sse.com.cn/disclosure/announcement/listing/stock/example.shtml",
            "SZSE": "https://www.szse.cn/disclosure/notice/example.html",
            "BSE": "https://www.bse.cn/disclosure/example.html",
        }[exchange],
        "document_sha256": "a" * 64,
        "record_locator": "page=1;table=1;row=1",
        "publication_date": publication_date,
        "publication_precision": publication_precision,
        "published_at": published_at,
        "available_at": published_at,
        "document_date": "2026-11-01",
        "captured_at": "2026-11-03T09:00:00+08:00",
        "is_bse_select_layer_transfer": False,
        "source_revision_id": f"revision-{dataset}",
        "source_contract_fingerprint": "b" * 64,
        "source_receipt_id": f"receipt-{dataset}",
    }


def _audit(
    rows: list[dict[str, object]],
    instruments: list[str],
    *,
    signal_date: str = "2026-11-03",
    decision_at: str = "2026-11-03T15:00:00+08:00",
) -> dict[str, object]:
    datasets = {str(row["source_dataset"]) for row in rows}
    return audit_listing_event_signal_coverage(
        pl.DataFrame(rows),
        instruments=instruments,
        source_identities={dataset: _source_identity(dataset) for dataset in datasets},
        signal_date=signal_date,
        decision_at=decision_at,
    )


def test_signal_coverage_passes_when_official_event_was_public_before_cutoff() -> None:
    report = _audit(
        [_event_row()],
        ["600000.SH"],
    )

    assert report["status"] == "PASS"
    assert report["available_by_decision"] == 1
    assert report["missing_evidence_count"] == 0
    assert report["not_yet_public_count"] == 0
    assert report["historical_universe_completeness_proven"] is False
    assert report["training_dataset_unlocked"] is False
    assert report["backtest_admitted"] is False


def test_date_precision_release_is_not_available_before_end_of_publication_day() -> None:
    report = _audit(
        [
            _event_row(
                publication_date="2026-11-03",
                publication_precision="date",
                published_at="2026-11-03T23:59:59+08:00",
            )
        ],
        ["600000.SH"],
    )

    assert report["status"] == "FAIL"
    assert report["available_by_decision"] == 0
    assert report["not_yet_public_symbols"] == ["600000.SH"]


def test_signal_coverage_fails_when_expected_instrument_has_no_event_evidence() -> None:
    report = _audit(
        [_event_row()],
        ["600000.SH", "000001.SZ"],
    )

    assert report["status"] == "FAIL"
    assert report["missing_evidence_symbols"] == ["000001.SZ"]
    assert report["by_exchange"][1]["missing_evidence"] == 1


def test_signal_coverage_fails_when_listing_event_is_after_signal_date() -> None:
    report = _audit(
        [
            _event_row(
                listing_date="2026-11-04",
                publication_date="2026-11-02",
                published_at="2026-11-02T11:00:00+08:00",
            )
        ],
        ["600000.SH"],
    )

    assert report["status"] == "FAIL"
    assert report["listing_after_signal_date_symbols"] == ["600000.SH"]


def test_signal_coverage_rejects_duplicate_expected_symbols() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        _audit([_event_row()], ["600000.SH", "600000.SH"])


def test_signal_coverage_requires_decision_cutoff_on_signal_date() -> None:
    with pytest.raises(ValueError, match="must fall on signal_date"):
        _audit(
            [_event_row()],
            ["600000.SH"],
            decision_at="2026-11-04T09:30:00+08:00",
        )
