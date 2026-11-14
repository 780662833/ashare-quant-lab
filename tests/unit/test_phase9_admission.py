from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.research.admission import admit_training_dataset


def valid_provenance(report):
    datasets = ("daily_bars", "adj_factors", "trading_status", "trading_calendar")
    identities = {
        name: {"revision_id": f"rev-{name}", "contract_fingerprint": f"sha-{name}"}
        for name in datasets
    }
    identities["universe"] = dict(identities["daily_bars"])
    identities["price_limit_rules"] = {
        "revision_id": "rev-limits", "contract_fingerprint": "sha-limits", "source": "fixture"
    }
    identities["financial_statement_items"] = {
        "revision_id": "rev-pit", "contract_fingerprint": "sha-pit"
    }
    receipts = [{
        "dataset": "daily_bars",
        "receipt": {
            "replayable": True,
            "dependencies": {name: identities[name] for name in datasets},
        },
    }]
    pit_dependencies = {name: identities[name] for name in datasets}
    pit_dependencies["financial_statement_items"] = identities["financial_statement_items"]
    receipts.append({
        "dataset": "financial_statement_items",
        "receipt": {"replayable": True, "receipt_id": "pit-adapter", "dependencies": pit_dependencies},
    })
    observed = {name: identities[name] for name in datasets}
    observed["financial_statement_items"] = identities["financial_statement_items"]
    return {
        "acceptance_report_id": report["report_id"],
        "acceptance_report_window": dict(report["window"]),
        "universe_profile": report["profile"],
        "source_revisions": identities,
        "source_receipts": receipts,
        "receipt_provenance": {"dependency_identities": observed},
        "acceptance_source_receipts": dict(report["receipts"]),
        "limit_evidence": {
            "source_revision_identity": identities["price_limit_rules"],
            "revision": "rev-limits",
        },
        "training_dataset_unlocked": False,
        "pit_fundamentals": {
            "items": ["roe", "net_profit"],
            "as_of_date": report["window"]["end"],
            "receipt_id": "pit-adapter",
            "source_revision_identity": identities["financial_statement_items"],
            "availability_policy": "announce_date_end_of_day_Asia/Shanghai",
        },
    }


def valid_report():
    return {
        "report_id": "acceptance-2024",
        "overall_status": "PASS",
        "training_dataset_unlocked": False,
        "window": {"start": "2024-01-01", "end": "2024-04-30"},
        "profile": "cn_a_sh_sz_research_v1",
        "pit_items": ["roe", "net_profit"],
        "receipts": {
            "historical_universe": {"replayable": True},
            "hfq": {"replayable": True},
            "pit": {"replayable": True},
        },
        "gates": [
            {"name": "historical_universe", "status": "PASS",
             "supported_window": {"start": "2018-01-01", "end": "2026-12-31"}},
            {"name": "historical_universe_receipt", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "PASS", "diagnostics_assessed": True,
             "missing_factor_rows": 0, "related_historical_events": 0},
            {"name": "point_in_time_fundamentals", "status": "PASS", "mode": "strict",
             "items": ["roe", "net_profit"],
             "missing_symbol_item_pairs": 0},
            {"name": "trading_status", "status": "PASS", "missing_status_rows": 0,
             "unknown_risk_warning_rows": 0, "contradictory_rows": 0,
             "excluded_or_unresolved_rows": 0},
        ],
    }


def frames():
    features = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05", "2024-01-12"],
        "instrument": ["600000.SH", "000001.SZ", "600000.SH"],
        "return_5": [0.01, 0.02, 0.03],
        "pit_roe": [0.1, 0.2, 0.1],
    })
    labels = pl.DataFrame({
        "signal_date": ["2024-01-05", "2024-01-05"],
        "instrument": ["600000.SH", "000001.SZ"],
        "label_end_date": ["2024-01-12", "2024-01-12"],
        "forward_return": [0.03, -0.01],
    })
    return features, labels


def admit(report=None, features=None, labels=None, cutoff="2024-01-15"):
    report = report or valid_report()
    features = features if features is not None else frames()[0]
    labels = labels if labels is not None else frames()[1]
    return admit_training_dataset(
        acceptance_report=report,
        source_provenance=valid_provenance(report),
        features=features,
        labels=labels,
        start_date="2024-01-01",
        end_date="2024-04-30",
        training_cutoff=cutoff,
        universe_profile="cn_a_sh_sz_research_v1",
        label_horizon_sessions=5,
    )


def test_exact_scope_passes_and_returns_auditable_admission_without_fitting():
    result = admit()
    assert result.admitted is True
    assert result.report_id == "acceptance-2024"
    assert result.feature_rows == 3
    assert result.label_rows == 2
    assert result.source_fingerprint
    assert not hasattr(result, "model")


def test_any_failed_gate_blocks_training_dataset_admission():
    report = valid_report()
    report["gates"][2]["status"] = "FAIL"
    result = admit(report=report)
    assert result.admitted is False
    assert any("hfq_adjustment" in reason for reason in result.rejection_reasons)


def test_acceptance_window_or_profile_mismatch_blocks_admission():
    report = valid_report()
    report["window"]["end"] = "2024-04-29"
    result = admit(report=report)
    assert result.admitted is False
    assert any("window" in reason for reason in result.rejection_reasons)


def test_missing_gate_and_duplicate_gate_are_rejected():
    report = valid_report()
    report["gates"] = report["gates"][:-1] + [report["gates"][0]]
    result = admit(report=report)
    assert result.admitted is False
    assert any("trading_status" in reason for reason in result.rejection_reasons)
    assert any("duplicates" in reason for reason in result.rejection_reasons)


def test_label_not_mature_before_cutoff_is_rejected():
    _, labels = frames()
    labels = labels.with_columns(pl.lit("2024-01-15").alias("label_end_date"))
    result = admit(labels=labels)
    assert result.admitted is False
    assert any("mature" in reason for reason in result.rejection_reasons)


def test_label_without_feature_key_is_rejected():
    features, labels = frames()
    features = features.filter(pl.col("instrument") != "000001.SZ")
    result = admit(features=features, labels=labels)
    assert result.admitted is False
    assert any("without matching features" in reason for reason in result.rejection_reasons)


def test_non_finite_label_and_duplicate_keys_are_rejected():
    features, labels = frames()
    labels = pl.concat([labels, labels.head(1)]).with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(float("nan")).otherwise(pl.col("forward_return")).alias("forward_return")
    )
    result = admit(features=features, labels=labels)
    assert result.admitted is False
    assert any("duplicate" in reason for reason in result.rejection_reasons)
    assert any("non-finite" in reason for reason in result.rejection_reasons)


def test_non_replayable_source_receipt_blocks_admission():
    report = valid_report()
    provenance = valid_provenance(report)
    provenance["source_receipts"][0]["receipt"]["replayable"] = False
    result = admit_training_dataset(
        acceptance_report=report, source_provenance=provenance,
        features=frames()[0], labels=frames()[1],
        start_date="2024-01-01", end_date="2024-04-30",
        training_cutoff="2024-01-15", universe_profile=report["profile"],
    )
    assert result.admitted is False
    assert any("source provenance is invalid" in reason for reason in result.rejection_reasons)


def test_invalid_date_and_invalid_horizon_are_rejected():
    report = valid_report()
    with pytest.raises(ValueError, match="canonical"):
        admit_training_dataset(
            acceptance_report=report, source_provenance=valid_provenance(report),
            features=frames()[0], labels=frames()[1],
            start_date="2024-1-1", end_date="2024-04-30",
            training_cutoff="2024-01-15", universe_profile=report["profile"],
        )
    with pytest.raises(ValueError, match="positive"):
        admit_training_dataset(
            acceptance_report=report, source_provenance=valid_provenance(report),
            features=frames()[0], labels=frames()[1],
            start_date="2024-01-01", end_date="2024-04-30",
            training_cutoff="2024-01-15", universe_profile=report["profile"],
            label_horizon_sessions=0,
        )


def test_missing_or_mismatched_pit_receipt_blocks_training_admission():
    report = valid_report()
    report["receipts"].pop("pit")
    result = admit(report=report)
    assert result.admitted is False
    assert any("pit receipt" in reason for reason in result.rejection_reasons)

    report = valid_report()
    provenance = valid_provenance(report)
    provenance["acceptance_source_receipts"]["pit"] = {"replayable": False}
    result = admit_training_dataset(
        acceptance_report=report,
        source_provenance=provenance,
        features=frames()[0],
        labels=frames()[1],
        start_date="2024-01-01",
        end_date="2024-04-30",
        training_cutoff="2024-01-15",
        universe_profile=report["profile"],
    )
    assert result.admitted is False
    assert any("acceptance receipts do not match" in reason for reason in result.rejection_reasons)
