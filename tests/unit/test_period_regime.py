from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment
from ashare_quant.research import period_regime


def make_experiment(tmp_path, dates):
    provenance = {
        "acceptance_report_id": "acceptance-1",
        "acceptance_report_window": {"start": dates[0], "end": dates[-1]},
        "universe_profile": "profile-v1",
        "source_revisions": {
            "daily_bars": "d1", "adj_factors": "a1", "universe": "u1",
            "trading_status": "s1", "trading_calendar": "c1",
            "price_limit_rules": "p1",
        },
        "source_receipts": {
            "historical_universe": {"replayable": True},
            "hfq": {"replayable": True},
        },
        "acceptance_gates": {
            "historical_universe": "PASS",
            "hfq_adjustment": "PASS",
            "point_in_time_fundamentals": "FAIL",
            "trading_status": "PASS",
        },
        "required_benchmark_gates": {
            "historical_universe": "PASS", "hfq_adjustment": "PASS",
            "trading_status": "PASS",
        },
        "scope": {"start_date": dates[0], "end_date": dates[-1]},
        "pit_fundamentals_used": False,
        "training_dataset_unlocked": False,
    }
    folds = [{
        "fold": {
            "fold_id": "fold_001", "test_start": dates[74],
            "test_end": dates[-1], "test_sessions": len(dates) - 74,
        },
        "report": {},
    }]
    manifest = {
        "experiment_id": "exp-p10-3",
        "data_provenance": provenance,
        "training_dataset_unlocked": False,
        "model_training_performed": False,
    }
    daily = pl.DataFrame({
        "date": dates[74:],
        "daily_return": [0.001] * (len(dates) - 74),
        "nav": [100_000.0 * (1.001 ** i) for i in range(len(dates) - 74)],
    })
    return VerifiedWalkForwardExperiment(
        path=Path(tmp_path) / "exp-p10-3",
        manifest=manifest,
        folds=folds,
        fold_table=pl.DataFrame(),
        oos_daily=daily,
        oos_orders=pl.DataFrame(),
        oos_holdings=pl.DataFrame(),
    ), provenance


def make_bundle(dates, provenance, *, daily_membership=True, trend=0.2, omit_symbol=None):
    adjusted = []
    membership = []
    for i, day in enumerate(dates):
        symbols = ["600000.SH", "000001.SZ", "000002.SZ"]
        if daily_membership or i % 5 == 4:
            for symbol in symbols:
                membership.append({"instrument": symbol, "date": day})
        for j, symbol in enumerate(symbols):
            if symbol == omit_symbol:
                continue
            value = 100.0 + i * trend + j
            adjusted.append({
                "instrument": symbol, "date": day,
                "adj_close": value, "adj_is_exact": True,
            })
    return ResearchDataBundle(
        execution_bars=pl.DataFrame(),
        adjusted_bars=pl.DataFrame(adjusted),
        universe_membership=pl.DataFrame(membership) if membership else pl.DataFrame(
            schema={"instrument": pl.String, "date": pl.String}
        ),
        trading_dates=dates,
        provenance=provenance,
    )


def sessions(count=200):
    result = []
    current = date(2020, 1, 1)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def test_period_and_regime_diagnostics_are_trailing_and_identity_bound(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)

    assert report["protocol_version"] == "phase10.3"
    assert report["periods"][0]["period_type"] == "walk_forward_fold"
    assert report["periods"][0]["sessions"] == 126
    assert report["periods"][0]["status"] == "OK"
    assert report["unknown_market_state_sessions"] == 0
    assert {item["regime"] for item in report["regimes"]} == {"BULL"}
    assert report["training_dataset_unlocked"] is False
    assert report["model_training_performed"] is False


def test_weekly_only_membership_is_not_forward_filled(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance, daily_membership=False)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)

    assert report["unknown_market_state_sessions"] > 0
    assert any("UNKNOWN" == item["regime"] for item in report["regimes"])
    assert "missing_daily_pit_universe_membership" in report["unknown_reasons"]


def test_bundle_source_revision_mismatch_is_rejected(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    changed = {**provenance, "source_revisions": {**provenance["source_revisions"], "adj_factors": "other"}}
    bundle = make_bundle(dates, changed)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    with pytest.raises(ValueError, match="source_revisions"):
        period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)


def test_failed_price_gate_blocks_period_regime_analysis(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bad_provenance = {
        **provenance,
        "acceptance_gates": {
            **provenance["acceptance_gates"], "historical_universe": "FAIL",
        },
        "required_benchmark_gates": {
            **provenance["required_benchmark_gates"], "historical_universe": "FAIL",
        },
    }
    experiment = VerifiedWalkForwardExperiment(
        path=experiment.path,
        manifest={**experiment.manifest, "data_provenance": bad_provenance},
        folds=experiment.folds,
        fold_table=experiment.fold_table,
        oos_daily=experiment.oos_daily,
        oos_orders=experiment.oos_orders,
        oos_holdings=experiment.oos_holdings,
    )
    bundle = make_bundle(dates, bad_provenance)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    with pytest.raises(ValueError, match="PRICE_ONLY_ELIGIBLE"):
        period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)


def test_insufficient_calendar_year_is_explicit(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = period_regime.build_period_regime_diagnostics(
        str(experiment.path), bundle, min_period_sessions=200,
    )

    assert report["periods"][0]["status"] == "INSUFFICIENT_SAMPLE"
    assert all(item["status"] == "INSUFFICIENT_SAMPLE" for item in report["periods"])



def test_bear_regime_requires_negative_median_and_weak_breadth(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance, trend=-0.2)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)

    assert {item["regime"] for item in report["regimes"]} == {"BEAR"}


def test_market_state_coverage_below_80_percent_is_unknown(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance, omit_symbol="000002.SZ")
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = period_regime.build_period_regime_diagnostics(str(experiment.path), bundle)

    assert report["unknown_market_state_sessions"] == 126
    assert "valid_universe_coverage_below_80_percent" in report["unknown_reasons"]
    unknown = next(item for item in report["regimes"] if item["regime"] == "UNKNOWN")
    assert unknown["mean_median_63_session_return"] is None
    assert unknown["mean_positive_breadth"] is None



def test_qa_report_includes_p10_3_period_and_regime_sections(tmp_path, monkeypatch):
    dates = sessions()
    experiment, provenance = make_experiment(tmp_path, dates)
    bundle = make_bundle(dates, provenance)
    from ashare_quant.research import qa as qa_module

    monkeypatch.setattr(qa_module, "verify_walk_forward_experiment", lambda _path: experiment)
    monkeypatch.setattr(period_regime, "verify_walk_forward_experiment", lambda _path: experiment)

    report = qa_module.build_research_qa_report(
        str(experiment.path),
        qa_id="qa-p10-3",
        created_at="2026-11-13T00:00:00Z",
        repository_commit="abcdef1234567",
        bundle=bundle,
    )

    assert report["protocol_version"] == "phase10.3"
    assert report["periods"]
    assert report["regimes"]
    assert report["integrity"]["period_regime_sha256"]
    assert report["conclusion"] == "INSUFFICIENT_EVIDENCE"
