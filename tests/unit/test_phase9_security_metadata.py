import polars as pl
import pytest

from ashare_quant.research.security_metadata import (
    audit_security_metadata_coverage,
    select_security_metadata_as_of,
    validate_security_metadata,
)

IDENTITIES = {
    "instruments": {
        "revision_id": "instruments-r1",
        "contract_fingerprint": "instruments-contract",
        "receipt_id": "instruments-receipt",
        "validated_evidence_classes": ["immutable_event"],
    },
    "industry_members": {
        "revision_id": "industry-r1",
        "contract_fingerprint": "industry-contract",
        "receipt_id": "industry-receipt",
        "validated_evidence_classes": ["strict_pit"],
    },
    "valuation_metrics": {
        "revision_id": "valuation-r1",
        "contract_fingerprint": "valuation-contract",
        "receipt_id": "valuation-receipt",
        "validated_evidence_classes": ["strict_pit"],
    },
}


def observation(
    field,
    *,
    instrument="600000.SH",
    effective_from,
    effective_to=None,
    available_at="2024-01-02T10:00:00+08:00",
    value_numeric=None,
    value_string=None,
    value_date=None,
    source_dataset=None,
    pit_grade=None,
    quality=None,
    evidence_class=None,
    is_exact=True,
    revision_id=None,
    contract_fingerprint=None,
    receipt_id=None,
):
    defaults = {
        "listing_date": (
            "instruments", "event_verified", "verified", "immutable_event"
        ),
        "industry_l1_code": (
            "industry_members", "strict", "strict", "strict_pit"
        ),
        "industry_l2_code": (
            "industry_members", "strict", "strict", "strict_pit"
        ),
        "industry_l3_code": (
            "industry_members", "strict", "strict", "strict_pit"
        ),
        "market_cap_total": (
            "valuation_metrics", "strict", "strict", "strict_pit"
        ),
        "market_cap_float": (
            "valuation_metrics", "strict", "strict", "strict_pit"
        ),
    }
    dataset, grade, row_quality, evidence = defaults[field]
    dataset = source_dataset or dataset
    identity = IDENTITIES[dataset]
    return {
        "instrument": instrument,
        "field": field,
        "value_numeric": value_numeric,
        "value_string": value_string,
        "value_date": value_date,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "available_at": available_at,
        "evidence_class": evidence_class or evidence,
        "pit_grade": pit_grade or grade,
        "quality": quality or row_quality,
        "is_exact": is_exact,
        "source": "synthetic-provider",
        "source_dataset": dataset,
        "source_revision_id": revision_id or identity["revision_id"],
        "source_contract_fingerprint": (
            contract_fingerprint or identity["contract_fingerprint"]
        ),
        "source_receipt_id": receipt_id or identity["receipt_id"],
    }


def frame(*rows):
    return pl.DataFrame(rows, schema_overrides={
        "value_numeric": pl.Float64,
        "value_string": pl.String,
        "value_date": pl.String,
        "effective_to": pl.String,
    })


def select(rows, *, signal_date="2024-01-05", decision_at="2024-01-05T16:00:00+08:00",
           instruments=("600000.SH",), fields=("industry_l1_code",)):
    return select_security_metadata_as_of(
        frame(*rows),
        source_identities=IDENTITIES,
        instruments=list(instruments),
        fields=list(fields),
        signal_date=signal_date,
        decision_at=decision_at,
    )


def test_selects_only_metadata_effective_and_available_by_signal_cutoff():
    rows = [
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to=None,
            available_at="2024-01-02T10:00:00+08:00",
            value_string="IND-A",
        ),
        observation(
            "listing_date",
            effective_from="2020-03-10",
            effective_to=None,
            available_at="2020-03-10T23:59:59+08:00",
            value_date="2020-03-10",
        ),
        observation(
            "market_cap_total",
            effective_from="2024-01-05",
            effective_to="2024-01-08",
            available_at="2024-01-05T15:30:00+08:00",
            value_numeric=125_000_000.0,
        ),
    ]
    selected = select(
        rows,
        fields=("industry_l1_code", "listing_date", "market_cap_total"),
    )
    values = {row["field"]: row for row in selected.to_dicts()}
    assert values["industry_l1_code"]["value_string"] == "IND-A"
    assert values["listing_date"]["value_date"] == "2020-03-10"
    assert values["market_cap_total"]["value_numeric"] == pytest.approx(125_000_000.0)


def test_does_not_use_metadata_published_after_decision_timestamp():
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-01-01",
        effective_to=None,
        available_at="2024-01-05T16:01:00+08:00",
        value_string="LATE",
    )]
    selected = select(rows)
    assert selected.is_empty()


def test_does_not_use_future_effective_industry_even_if_already_announced():
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-02-01",
        effective_to=None,
        available_at="2024-01-20T10:00:00+08:00",
        value_string="FUTURE",
    )]
    selected = select(rows, signal_date="2024-01-31",
                      decision_at="2024-01-31T16:00:00+08:00")
    assert selected.is_empty()


def test_uses_latest_revision_available_at_each_historical_cutoff():
    rows = [
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to=None,
            available_at="2024-01-01T09:00:00+08:00",
            value_string="OLD",
        ),
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to=None,
            available_at="2024-01-04T09:00:00+08:00",
            value_string="RESTATED",
        ),
    ]
    early = select(rows, signal_date="2024-01-03",
                   decision_at="2024-01-03T16:00:00+08:00")
    later = select(rows)
    assert early["value_string"].item() == "OLD"
    assert later["value_string"].item() == "RESTATED"


def test_revision_closing_an_old_interval_is_applied_before_effective_filter():
    rows = [
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to=None,
            available_at="2024-01-01T09:00:00+08:00",
            value_string="OLD",
        ),
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to="2024-01-05",
            available_at="2024-01-04T09:00:00+08:00",
            value_string="OLD",
        ),
        observation(
            "industry_l1_code",
            effective_from="2024-01-05",
            effective_to=None,
            available_at="2024-01-04T09:00:00+08:00",
            value_string="NEW",
        ),
    ]
    early = select(rows, signal_date="2024-01-03",
                   decision_at="2024-01-03T16:00:00+08:00")
    later = select(rows)
    assert early["value_string"].item() == "OLD"
    assert later["value_string"].item() == "NEW"


def test_market_cap_is_same_session_only_and_never_carried_forward():
    rows = [observation(
        "market_cap_float",
        effective_from="2024-01-05",
        effective_to="2024-01-08",
        available_at="2024-01-05T15:30:00+08:00",
        value_numeric=80_000_000.0,
    )]
    selected = select(
        rows,
        signal_date="2024-01-08",
        decision_at="2024-01-08T16:00:00+08:00",
        fields=("market_cap_float",),
    )
    assert selected.is_empty()


def test_rejects_overlap_after_revision_resolution():
    rows = [
        observation(
            "industry_l1_code",
            effective_from="2024-01-01",
            effective_to="2024-01-10",
            available_at="2024-01-01T09:00:00+08:00",
            value_string="A",
        ),
        observation(
            "industry_l1_code",
            effective_from="2024-01-05",
            effective_to=None,
            available_at="2024-01-04T09:00:00+08:00",
            value_string="B",
        ),
    ]
    with pytest.raises(ValueError, match="overlapping effective intervals"):
        select(rows, signal_date="2024-01-06",
               decision_at="2024-01-06T16:00:00+08:00")


def test_rejects_non_pit_industry_snapshot_quality():
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-01-01",
        effective_to=None,
        value_string="SNAPSHOT",
        pit_grade="none",
        quality="not_applicable",
    )]
    with pytest.raises(ValueError, match="required source quality"):
        validate_security_metadata(frame(*rows), source_identities=IDENTITIES)


def test_rejects_unbound_or_drifted_source_receipt_identity():
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-01-01",
        effective_to=None,
        value_string="IND-A",
        contract_fingerprint="wrong-contract",
    )]
    with pytest.raises(ValueError, match="source receipt identity"):
        validate_security_metadata(frame(*rows), source_identities=IDENTITIES)


def test_rejects_timezone_naive_publication_timestamp():
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-01-01",
        effective_to=None,
        available_at="2024-01-02T10:00:00",
        value_string="IND-A",
    )]
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_security_metadata(frame(*rows), source_identities=IDENTITIES)


def test_rejects_market_cap_without_bounded_validity():
    rows = [observation(
        "market_cap_total",
        effective_from="2024-01-05",
        effective_to=None,
        value_numeric=1_000_000.0,
    )]
    with pytest.raises(ValueError, match="bounded effective interval"):
        validate_security_metadata(frame(*rows), source_identities=IDENTITIES)


def test_coverage_audit_fails_closed_on_missing_instrument_field_pair():
    rows = [observation(
        "listing_date",
        effective_from="2020-03-10",
        effective_to=None,
        available_at="2020-03-10T23:59:59+08:00",
        value_date="2020-03-10",
    )]
    selected = select(rows, fields=("listing_date",))
    report = audit_security_metadata_coverage(
        selected,
        instruments=("600000.SH", "000001.SZ"),
        fields=("listing_date",),
    )
    assert report["status"] == "FAIL"
    assert report["expected_pairs"] == 2
    assert report["observed_pairs"] == 1
    assert report["missing_pairs"] == 1


def test_rejects_non_finite_market_cap_values():
    rows = [observation(
        "market_cap_total",
        effective_from="2024-01-05",
        effective_to="2024-01-08",
        available_at="2024-01-05T15:30:00+08:00",
        value_numeric=float("inf"),
    )]
    with pytest.raises(ValueError, match="finite and non-negative"):
        validate_security_metadata(frame(*rows), source_identities=IDENTITIES)


def test_rejects_claimed_quality_without_source_capability_attestation():
    identities = {
        **IDENTITIES,
        "industry_members": {
            **IDENTITIES["industry_members"],
            "validated_evidence_classes": ["snapshot_only"],
        },
    }
    rows = [observation(
        "industry_l1_code",
        effective_from="2024-01-01",
        effective_to=None,
        value_string="IND-A",
    )]
    with pytest.raises(ValueError, match="does not certify strict_pit"):
        validate_security_metadata(frame(*rows), source_identities=identities)
