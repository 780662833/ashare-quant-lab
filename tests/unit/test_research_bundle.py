from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.backtest import BacktestConfig
from ashare_quant.research import (
    ResearchBundleError,
    build_research_data_bundle,
    run_momentum_benchmark,
)
from ashare_quant.strategies.momentum import MomentumConfig


PROFILE = "cn_a_sh_sz_research_v1"


def make_sessions(count=12):
    sessions = []
    current = date(2024, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return sessions


def make_inputs(count=12):
    sessions = make_sessions(count)
    raw = []
    adjusted = []
    status = []
    membership = []
    for i, session in enumerate(sessions):
        for symbol, base, slope in (
            ("600000.SH", 10.0, 0.10),
            ("000001.SZ", 20.0, 0.02),
        ):
            close = base + slope * i
            raw.append({
                "instrument": symbol,
                "date": session,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 100000,
                "amount": close * 100000,
            })
            adjusted.append({
                "instrument": symbol,
                "date": session,
                "adj_close": close,
                "adj_is_exact": True,
            })
            status.append({
                "instrument": symbol,
                "date": session,
                "is_suspended": False,
                "is_limit_up_locked": False,
                "is_limit_down_locked": False,
                "limit_up": close * 1.20,
                "limit_down": close * 0.80,
                "risk_warning_known": True,
                "limit_evidence_source": "synthetic_rule_provider",
                "limit_rule_version": "synthetic-rules-v1",
                "limit_regime": "synthetic_limited_session",
            })
    # Snapshots are required on every final available session in an ISO week.
    seen = {}
    for session in sessions:
        year, week, _ = date.fromisoformat(session).isocalendar()
        seen[(year, week)] = session
    weekly_dates = set(seen.values())
    for session in sorted(weekly_dates):
        for symbol in ("600000.SH", "000001.SZ"):
            membership.append({"instrument": symbol, "date": session})
    report = {
        "created_at": "2026-11-04T00:00:00Z",
        "window": {"start": sessions[0], "end": sessions[-1]},
        "profile": PROFILE,
        "training_dataset_unlocked": False,
        "gates": [
            {"name": "historical_universe", "status": "PASS"},
            {"name": "historical_universe_receipt", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "PASS"},
            {"name": "point_in_time_fundamentals", "status": "FAIL"},
            {"name": "trading_status", "status": "PASS"},
        ],
        "receipts": {
            "historical_universe": {"revision": "u1", "replayable": True},
            "hfq": {"revision": "a1", "replayable": True},
        },
    }
    revisions = {
        "daily_bars": "daily-v1",
        "adj_factors": "hfq-v1",
        "instruments": "instruments-v1",
        "trading_status": "status-v1",
        "trading_calendar": "calendar-v1",
        "price_limit_rules": "synthetic-rules-v1",
    }
    required_sources = (
        "daily_bars", "adj_factors", "instruments",
        "trading_status", "trading_calendar", "price_limit_rules",
    )
    source_receipts = []
    coverage_audits = {}
    for dataset in required_sources:
        receipt_id = f"synthetic-receipt-{dataset}"
        fingerprint = f"synthetic-contract-{dataset}"
        source_receipts.append({
            "dataset": dataset,
            "receipt": {
                "replayable": True,
                "receipt_id": receipt_id,
                "coverage": {"completeness": "not_assessed"},
                "dependencies": {
                    dataset: {
                        "revision_id": revisions[dataset],
                        "contract_fingerprint": fingerprint,
                    }
                },
            },
        })
        coverage_audits[dataset] = {
            "status": "PASS",
            "window": {"start": sessions[0], "end": sessions[-1]},
            "scope": f"synthetic full {dataset} test scope",
            "method": "synthetic deterministic fixture audit",
            "receipt_id": receipt_id,
            "revision_id": revisions[dataset],
            "contract_fingerprint": fingerprint,
            "expected_rows": 10,
            "observed_rows": 10,
            "missing_rows": 0,
            "duplicate_rows": 0,
        }
    return {
        "raw_bars": pl.DataFrame(raw),
        "adjusted_bars": pl.DataFrame(adjusted),
        "universe_membership": pl.DataFrame(membership),
        "execution_evidence": pl.DataFrame(status),
        "trading_dates": sessions,
        "acceptance_report": report,
        "acceptance_report_id": "synthetic-acceptance-v1",
        "source_revisions": revisions,
        "source_receipts": source_receipts,
        "coverage_audits": coverage_audits,
        "start_date": sessions[0],
        "end_date": sessions[-1],
        "universe_profile": PROFILE,
    }


def build(**overrides):
    inputs = make_inputs()
    inputs.update(overrides)
    return build_research_data_bundle(**inputs)


def test_bundle_joins_raw_execution_state_and_keeps_adjusted_prices_separate():
    bundle = build()
    assert bundle.execution_bars.columns == [
        "instrument", "date", "open", "high", "low", "close", "volume", "amount",
        "is_suspended", "is_limit_up_locked", "is_limit_down_locked", "limit_up", "limit_down",
    ]
    assert "adj_close" not in bundle.execution_bars.columns
    assert "open" not in bundle.adjusted_bars.columns
    assert bundle.provenance["training_dataset_unlocked"] is False
    assert bundle.provenance["pit_fundamentals_used"] is False


def test_price_only_benchmark_does_not_require_fundamental_pit_pass():
    bundle = build()
    assert bundle.provenance["required_benchmark_gates"] == {
        "historical_universe": "PASS",
        "hfq_adjustment": "PASS",
        "trading_status": "PASS",
    }


def test_bundle_preserves_all_four_independent_acceptance_gate_statuses():
    bundle = build()
    assert bundle.provenance["acceptance_gates"] == {
        "historical_universe": "PASS",
        "historical_universe_receipt": "PASS",
        "hfq_adjustment": "PASS",
        "point_in_time_fundamentals": "FAIL",
        "trading_status": "PASS",
    }


def test_bundle_rejects_missing_historical_universe_receipt_gate():
    inputs = make_inputs()
    inputs["acceptance_report"]["gates"] = [
        gate for gate in inputs["acceptance_report"]["gates"]
        if gate["name"] != "historical_universe_receipt"
    ]
    with pytest.raises(ResearchBundleError, match="missing independent gates"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_failed_historical_universe_receipt_gate():
    inputs = make_inputs()
    inputs["acceptance_report"]["gates"] = [
        {**gate, "status": "FAIL"}
        if gate["name"] == "historical_universe_receipt" else gate
        for gate in inputs["acceptance_report"]["gates"]
    ]
    with pytest.raises(ResearchBundleError, match="historical_universe_receipt"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_missing_independent_pit_gate():
    inputs = make_inputs()
    inputs["acceptance_report"]["gates"] = [
        gate for gate in inputs["acceptance_report"]["gates"]
        if gate["name"] != "point_in_time_fundamentals"
    ]
    with pytest.raises(ResearchBundleError, match="missing independent gates"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_missing_status_row():
    inputs = make_inputs()
    inputs["execution_evidence"] = inputs["execution_evidence"].slice(1)
    with pytest.raises(ResearchBundleError, match="key scope differs"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_locked_limit_without_price_when_passed_directly():
    inputs = make_inputs()
    inputs["execution_evidence"] = inputs["execution_evidence"].with_columns(
        pl.lit(True).alias("is_limit_up_locked"),
        pl.lit(None, dtype=pl.Float64).alias("limit_up"),
    )
    with pytest.raises(ResearchBundleError, match="Limit-up lock flag lacks"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_non_exact_adjusted_price():
    inputs = make_inputs()
    inputs["adjusted_bars"] = inputs["adjusted_bars"].with_columns(
        pl.when(pl.col("instrument") == "600000.SH")
        .then(False)
        .otherwise(pl.col("adj_is_exact"))
        .alias("adj_is_exact")
    )
    with pytest.raises(ResearchBundleError, match="non-positive or non-exact"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_missing_weekly_point_in_time_membership():
    inputs = make_inputs()
    first_weekly_date = min(inputs["universe_membership"]["date"].to_list())
    inputs["universe_membership"] = inputs["universe_membership"].filter(
        pl.col("date") != first_weekly_date
    )
    with pytest.raises(ResearchBundleError, match="missing weekly signal dates"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_failed_required_gate_but_allows_pit_failure_for_momentum():
    inputs = make_inputs()
    gates = inputs["acceptance_report"]["gates"]
    inputs["acceptance_report"]["gates"] = [
        {**gate, "status": "FAIL"} if gate["name"] == "hfq_adjustment" else gate
        for gate in gates
    ]
    with pytest.raises(ResearchBundleError, match="hfq_adjustment"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_window_mismatch_and_duplicate_calendar():
    inputs = make_inputs()
    inputs["acceptance_report"] = {
        **inputs["acceptance_report"],
        "window": {"start": "2023-12-31", "end": inputs["end_date"]},
    }
    with pytest.raises(ResearchBundleError, match="window"):
        build_research_data_bundle(**inputs)

    inputs = make_inputs()
    inputs["trading_dates"] = inputs["trading_dates"] + [inputs["trading_dates"][0]]
    with pytest.raises(ResearchBundleError, match="unique"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_missing_replayable_source_receipt():
    inputs = make_inputs()
    inputs["acceptance_report"] = {
        **inputs["acceptance_report"],
        "receipts": {"hfq": {"revision": "a1"}},
    }
    with pytest.raises(ResearchBundleError, match="historical_universe receipt"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_membership_snapshot_outside_exchange_calendar():
    inputs = make_inputs()
    extra = pl.DataFrame([{"instrument": "600000.SH", "date": "2024-01-06"}])
    inputs["universe_membership"] = pl.concat([inputs["universe_membership"], extra])
    with pytest.raises(ResearchBundleError, match="outside trading calendar"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_incomplete_source_revision_metadata():
    inputs = make_inputs()
    inputs["source_revisions"] = {}
    with pytest.raises(ResearchBundleError, match="source_revisions"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_source_revision_not_bound_to_receipt():
    inputs = make_inputs()
    inputs["source_revisions"]["daily_bars"] = "caller-invented-revision"
    with pytest.raises(ResearchBundleError, match="do not match receipt dependencies"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_not_assessed_coverage_without_a_passing_audit():
    inputs = make_inputs()
    inputs["coverage_audits"].pop("daily_bars")
    with pytest.raises(ResearchBundleError, match="Missing dataset-specific coverage audit"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_coverage_audit_with_missing_rows():
    inputs = make_inputs()
    inputs["coverage_audits"]["trading_status"]["missing_rows"] = 1
    with pytest.raises(ResearchBundleError, match="incomplete or inconsistent"):
        build_research_data_bundle(**inputs)


def test_bundle_rejects_coverage_audit_bound_to_different_receipt_revision():
    inputs = make_inputs()
    inputs["coverage_audits"]["price_limit_rules"]["revision_id"] = "unrelated-revision"
    with pytest.raises(ResearchBundleError, match="revision mismatch"):
        build_research_data_bundle(**inputs)


def test_momentum_benchmark_runs_from_bundle_through_shared_backtest_and_report():
    inputs = make_inputs(count=40)
    bundle = build_research_data_bundle(**inputs)
    run = run_momentum_benchmark(
        bundle,
        strategy_config=MomentumConfig(top_n=2, lookback_sessions=8, skip_recent_sessions=2),
        backtest_config=BacktestConfig(initial_cash=100_000, top_n=2),
    )
    assert run.targets.height > 0
    assert run.result.daily.height == len(inputs["trading_dates"])
    assert run.result.orders.height > 0
    assert run.report["period"]["start"] == inputs["start_date"]
    assert run.report["execution"]["order_count"] == run.result.orders.height
    assert run.provenance["acceptance_report_id"] == "synthetic-acceptance-v1"


def test_momentum_orchestration_rejects_top_n_mismatch():
    bundle = build()
    with pytest.raises(ValueError, match="must match"):
        run_momentum_benchmark(
            bundle,
            strategy_config=MomentumConfig(top_n=2, lookback_sessions=2, skip_recent_sessions=1),
            backtest_config=BacktestConfig(top_n=20),
        )
