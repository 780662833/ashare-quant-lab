from __future__ import annotations

from copy import deepcopy

import polars as pl
import pytest

from ashare_quant.data.cne_adapter import CNEquityAdapter
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.cnequity_assembler import assemble_cnequity_research_bundle

START = "2024-01-02"
END = "2024-01-05"
PROFILE = "cn_a_sh_sz_research_v1"

def _fixtures():
    sessions = [START, "2024-01-03", "2024-01-04", END]
    symbols = ["600000.SH", "000001.SZ"]
    raw_rows, adjusted_rows, status_rows, limit_rows = [], [], [], []
    for index, day in enumerate(sessions):
        for symbol_index, symbol in enumerate(symbols):
            close = 10.0 + index * 0.1 + symbol_index
            raw_rows.append({
                "instrument": symbol, "date": day, "open": close,
                "high": close * 1.01, "low": close * 0.99, "close": close,
                "volume": 100_000.0, "amount": close * 100_000.0,
            })
            adjusted_rows.append({
                "instrument": symbol, "date": day,
                "adj_close": close * 0.8, "adj_is_exact": True,
            })
            status_rows.append({
                "symbol": symbol, "trade_date": day, "is_trading": True,
                "status": "normal", "risk_warning": False,
            })
            limit_rows.append({
                "symbol": symbol, "trade_date": day,
                "is_limit_up_locked": False, "is_limit_down_locked": False,
                "limit_up": close * 1.2, "limit_down": close * 0.8,
                "source": "synthetic-limit-provider",
                "rule_version": "limit-r1", "limit_regime": "synthetic-main-board",
            })
    bars = pl.DataFrame(raw_rows).join(
        pl.DataFrame(adjusted_rows), on=["instrument", "date"], how="left"
    )
    dependencies = {
        "daily_bars": {"revision_id": "daily-r1", "contract_fingerprint": "daily-contract"},
        "adj_factors": {"revision_id": "hfq-r1", "contract_fingerprint": "hfq-contract"},
        "trading_status": {"revision_id": "status-r1", "contract_fingerprint": "status-contract"},
        "trading_calendar": {"revision_id": "calendar-r1", "contract_fingerprint": "calendar-contract"},
    }
    receipts = [
        {"dataset": "daily_bars", "receipt": {"replayable": True, "receipt_id": "daily-receipt", "dependencies": {
            "daily_bars": dependencies["daily_bars"], "adj_factors": dependencies["adj_factors"],
        }}},
        {"dataset": "trading_status", "receipt": {"replayable": True, "receipt_id": "status-receipt", "dependencies": {
            "trading_status": dependencies["trading_status"],
        }}},
        {"dataset": "trading_calendar", "receipt": {"replayable": True, "receipt_id": "calendar-receipt", "dependencies": {
            "trading_calendar": dependencies["trading_calendar"],
        }}},
    ]
    revisions = {
        **dependencies,
        "universe": dependencies["daily_bars"],
        "price_limit_rules": {
            "revision_id": "limit-r1", "contract_fingerprint": "limit-contract",
            "source": "synthetic-limit-provider",
        },
    }
    weekly_dates = [END]
    membership = pl.DataFrame([
        {"instrument": symbol, "date": day}
        for day in weekly_dates for symbol in symbols
    ])
    acceptance = {
        "window": {"start": START, "end": END}, "profile": PROFILE,
        "training_dataset_unlocked": False,
        "gates": [
            {"name": "historical_universe", "status": "PASS"},
            {"name": "historical_universe_receipt", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "PASS"},
            {"name": "point_in_time_fundamentals", "status": "FAIL"},
            {"name": "trading_status", "status": "PASS"},
        ],
        "receipts": {
            "historical_universe": {"replayable": True, "receipt_id": "universe-acceptance"},
            "hfq": {"replayable": True, "receipt_id": "hfq-acceptance"},
        },
    }
    counts = {
        "daily_bars": len(raw_rows), "adj_factors": len(adjusted_rows),
        "universe": membership.height, "trading_status": len(status_rows),
        "trading_calendar": len(sessions), "price_limit_rules": len(limit_rows),
    }
    audits = {
        name: {
            "audit_id": f"synthetic-{name}", "status": "PASS",
            "window": {"start": START, "end": END},
            "expected_rows": count, "observed_rows": count,
            "missing_keys": 0, "unexpected_keys": 0,
            "method": "synthetic exact-key reconciliation",
            "source": "synthetic fixture only",
        }
        for name, count in counts.items()
    }
    return {
        "sessions": sessions, "symbols": symbols, "bars": bars,
        "status": pl.DataFrame(status_rows), "limits": pl.DataFrame(limit_rows),
        "membership": membership, "receipts": receipts, "dependencies": dependencies,
        "revisions": revisions, "acceptance": acceptance, "audits": audits,
    }

class FakeAdapter(CNEquityAdapter):
    def __post_init__(self):
        # Keep this adapter contract test independent of an installed CNEquity package/lake.
        pass

    def __init__(self, fixture):
        super().__init__(data_root="synthetic-only")
        self.fixture = fixture

    @property
    def source_receipts(self):
        return deepcopy(self.fixture["receipts"])

    @property
    def source_revision_map(self):
        return {name: identity["revision_id"] for name, identity in self.fixture["dependencies"].items()}

    @property
    def source_contract_fingerprints(self):
        return {name: identity["contract_fingerprint"] for name, identity in self.fixture["dependencies"].items()}

    def trading_calendar(self, start_date, end_date):
        return list(self.fixture["sessions"])

    def daily_bars(self, instruments, start_date, end_date):
        return self.fixture["bars"]

    def point_in_time_universe(self, as_of_date):
        return pl.DataFrame({"instrument": self.fixture["symbols"]})

    def trading_status(self, instruments, start_date, end_date):
        return self.fixture["status"]


def assemble(fixture):
    return assemble_cnequity_research_bundle(
        FakeAdapter(fixture),
        start_date=START,
        end_date=END,
        acceptance_report=fixture["acceptance"],
        acceptance_report_id="synthetic-acceptance-001",
        price_limit_evidence=fixture["limits"],
        price_limit_revision=fixture["revisions"]["price_limit_rules"],
        coverage_audits=fixture["audits"],
    )


def test_assembler_builds_bundle_from_receipt_pinned_sources():
    fixture = _fixtures()
    bundle = assemble(fixture)

    assert bundle.execution_bars.height == 8
    assert bundle.adjusted_bars.height == 8
    assert bundle.universe_membership.height == 2
    assert bundle.trading_dates == fixture["sessions"]
    assert bundle.provenance["source_revisions"]["daily_bars"]["revision_id"] == "daily-r1"
    assert bundle.provenance["receipt_provenance"]["dependency_identities"] == fixture["dependencies"]
    assert bundle.provenance["acceptance_gates"]["point_in_time_fundamentals"] == "FAIL"
    assert bundle.provenance["training_dataset_unlocked"] is False


def test_assembler_fails_closed_when_limit_evidence_is_missing():
    fixture = _fixtures()
    fixture["limits"] = fixture["limits"].head(7)
    with pytest.raises(ResearchBundleError, match="Price-limit evidence missing"):
        assemble(fixture)


def test_assembler_requires_receipt_for_each_required_dependency():
    fixture = _fixtures()
    fixture["receipts"] = [
        record for record in fixture["receipts"] if record["dataset"] != "trading_calendar"
    ]
    with pytest.raises(ResearchBundleError, match="do not cover required dependencies"):
        assemble(fixture)


def test_assembler_requires_audit_for_every_source():
    fixture = _fixtures()
    fixture["audits"].pop("price_limit_rules")
    with pytest.raises(ResearchBundleError, match="coverage_audits missing"):
        assemble(fixture)


def test_assembler_rejects_rule_revision_mismatch():
    fixture = _fixtures()
    fixture["limits"] = fixture["limits"].with_columns(
        pl.lit("caller-invented-r9").alias("rule_version")
    )
    with pytest.raises(ResearchBundleError, match="revision identity"):
        assemble(fixture)
