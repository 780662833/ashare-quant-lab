from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.backtest import BacktestConfig
from ashare_quant.research import (
    ResearchBundleError,
    WalkForwardConfig,
    build_research_data_bundle,
    build_walk_forward_folds,
    run_walk_forward_experiments,
    run_walk_forward_momentum,
)
from ashare_quant.strategies.momentum import MomentumConfig

PROFILE = "cn_a_sh_sz_research_v1"


def make_sessions(count: int) -> list[str]:
    result = []
    current = date(2023, 1, 2)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def make_bundle(count=70):
    sessions = make_sessions(count)
    symbols = [
        ("600000.SH", 10.0, 0.18),
        ("000001.SZ", 20.0, 0.09),
        ("600519.SH", 30.0, 0.03),
    ]
    raw, adjusted, state = [], [], []
    for index, day in enumerate(sessions):
        for symbol, base, slope in symbols:
            close = base + slope * index + 0.03 * (index % 5)
            raw.append({
                "instrument": symbol, "date": day,
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "volume": 100_000, "amount": close * 100_000,
            })
            adjusted.append({
                "instrument": symbol, "date": day,
                "adj_close": close, "adj_is_exact": True,
            })
            state.append({
                "instrument": symbol, "date": day,
                "is_suspended": False, "is_limit_up_locked": False,
                "is_limit_down_locked": False, "limit_up": close * 1.20,
                "limit_down": close * 0.80, "risk_warning_known": True,
                "limit_evidence_source": "synthetic_rule_source",
                "limit_rule_version": "synthetic-rule-v1",
                "limit_regime": "synthetic_limited",
            })
    last_by_week = {}
    for day in sessions:
        iso = date.fromisoformat(day).isocalendar()
        last_by_week[(iso.year, iso.week)] = day
    membership = [
        {"instrument": symbol, "date": day}
        for day in sorted(last_by_week.values())
        for symbol, _, _ in symbols
    ]
    report = {
        "created_at": "2026-11-04T00:00:00Z",
        "window": {"start": sessions[0], "end": sessions[-1]},
        "profile": PROFILE,
        "training_dataset_unlocked": False,
        "gates": [
            {"name": "historical_universe", "status": "PASS"},
            {"name": "hfq_adjustment", "status": "PASS"},
            {"name": "point_in_time_fundamentals", "status": "FAIL"},
            {"name": "trading_status", "status": "PASS"},
        ],
        "receipts": {
            "historical_universe": {"revision": "universe-1", "replayable": True},
            "hfq": {"revision": "hfq-1", "replayable": True},
        },
    }
    revisions = {
        "daily_bars": "daily-1",
        "adj_factors": "hfq-1",
        "universe": "universe-1",
        "trading_status": "status-1",
        "trading_calendar": "calendar-1",
        "price_limit_rules": "limits-1",
    }
    return build_research_data_bundle(
        raw_bars=pl.DataFrame(raw),
        adjusted_bars=pl.DataFrame(adjusted),
        universe_membership=pl.DataFrame(membership),
        execution_evidence=pl.DataFrame(state),
        trading_dates=sessions,
        acceptance_report=report,
        acceptance_report_id=f"synthetic-{count}",
        source_revisions=revisions,
        start_date=sessions[0],
        end_date=sessions[-1],
        universe_profile=PROFILE,
    )


def make_folds(bundle):
    return build_walk_forward_folds(
        bundle.trading_dates,
        WalkForwardConfig(
            min_train_sessions=15,
            validation_sessions=5,
            test_sessions=10,
            label_horizon_sessions=1,
            embargo_sessions=0,
            expanding_train=True,
        ),
    )


def empty_targets():
    return pl.DataFrame(
        schema={"signal_date": pl.String, "instrument": pl.String, "weight": pl.Float64}
    )


def test_walk_forward_momentum_returns_fold_local_oos_reports_and_manifest():
    bundle = make_bundle(70)
    folds = make_folds(bundle)[:2]
    run = run_walk_forward_momentum(
        bundle,
        folds,
        strategy_config=MomentumConfig(top_n=2, lookback_sessions=8, skip_recent_sessions=2),
        backtest_config=BacktestConfig(initial_cash=100_000, top_n=2),
    )
    assert len(run.fold_results) == len(folds)
    assert set(run.summaries) == {"momentum"}
    assert run.summaries["momentum"]["fold_count"] == len(folds)
    assert run.manifest["training_dataset_unlocked"] is False
    assert run.manifest["continuous_nav_constructed"] is False
    for item in run.fold_results:
        assert item.report["period"] == {
            "start": item.fold.test_start,
            "end": item.fold.test_end,
            "sessions": item.fold.test_sessions,
        }
        assert item.provenance["fold"]["fold_id"] == item.fold.fold_id
        assert item.provenance["signal_cutoff"] == item.fold.test_end
        assert item.targets["signal_date"].max() < item.fold.test_end


def test_future_rows_after_test_end_do_not_change_fold_targets_or_metrics():
    small = make_bundle(60)
    large = make_bundle(75)
    fold = make_folds(small)[0]
    config = MomentumConfig(top_n=2, lookback_sessions=8, skip_recent_sessions=2)
    bt_config = BacktestConfig(initial_cash=100_000, top_n=2)
    small_run = run_walk_forward_momentum(
        small, [fold], strategy_config=config, backtest_config=bt_config
    )
    large_run = run_walk_forward_momentum(
        large, [fold], strategy_config=config, backtest_config=bt_config
    )
    first, second = small_run.fold_results[0], large_run.fold_results[0]
    assert first.targets.equals(second.targets)
    assert first.result.daily.equals(second.result.daily)
    assert first.report["total_return"] == pytest.approx(second.report["total_return"])


def test_runner_executes_multiple_strategies_on_exact_same_fold_windows():
    bundle = make_bundle(70)
    folds = make_folds(bundle)[:2]
    targets = {}
    for strategy in ("candidate_model", "momentum"):
        targets[strategy] = {}
        for fold in folds:
            signal_dates = [
                day for day in bundle.trading_dates
                if fold.test_start <= day < fold.test_end
                and date.fromisoformat(day).weekday() == 4
            ]
            rows = []
            for signal_date in signal_dates:
                rows.extend([
                    {"signal_date": signal_date, "instrument": "600000.SH", "weight": 0.5},
                    {"signal_date": signal_date, "instrument": "000001.SZ", "weight": 0.5},
                ])
            targets[strategy][fold.fold_id] = pl.DataFrame(
                rows,
                schema={"signal_date": pl.String, "instrument": pl.String, "weight": pl.Float64},
            )
    run = run_walk_forward_experiments(
        bundle, folds, targets,
        backtest_config=BacktestConfig(initial_cash=100_000, top_n=2),
    )
    assert set(run.summaries) == {"candidate_model", "momentum"}
    assert len(run.fold_results) == len(folds) * 2
    for fold in folds:
        rows = [item for item in run.fold_results if item.fold.fold_id == fold.fold_id]
        assert len(rows) == 2
        assert {item.report["period"]["start"] for item in rows} == {fold.test_start}
        assert {item.report["period"]["end"] for item in rows} == {fold.test_end}


def test_runner_rejects_targets_outside_oos_or_on_final_signal_day():
    bundle = make_bundle(70)
    fold = make_folds(bundle)[0]
    invalid = pl.DataFrame({
        "signal_date": [fold.test_end],
        "instrument": ["600000.SH"],
        "weight": [1.0],
    })
    with pytest.raises(ResearchBundleError, match="outside executable test window"):
        run_walk_forward_experiments(
            bundle, [fold], {"strategy": {fold.fold_id: invalid}},
            backtest_config=BacktestConfig(top_n=1),
        )


def test_runner_rejects_missing_fold_target_map():
    bundle = make_bundle(70)
    folds = make_folds(bundle)[:2]
    with pytest.raises(ResearchBundleError, match="fold target keys mismatch"):
        run_walk_forward_experiments(
            bundle, folds,
            {"strategy": {folds[0].fold_id: empty_targets()}},
        )


def test_runner_rejects_invalid_fold_boundaries_and_overlapping_test_windows():
    bundle = make_bundle(70)
    folds = make_folds(bundle)[:2]
    first, second = folds
    invalid = type(first)(**{
        **first.to_dict(),
        "test_start": first.validation_end,
    })
    with pytest.raises(ResearchBundleError, match="invalid chronological"):
        run_walk_forward_experiments(
            bundle, [invalid],
            {"strategy": {invalid.fold_id: empty_targets()}},
        )

    overlapping = type(second)(**{
        **second.to_dict(),
        "test_start": first.test_end,
    })
    with pytest.raises(ResearchBundleError, match="must not overlap"):
        run_walk_forward_experiments(
            bundle, [first, overlapping],
            {"strategy": {
                first.fold_id: empty_targets(),
                overlapping.fold_id: empty_targets(),
            }},
        )


def test_fold_summary_does_not_compound_independent_nav_paths():
    bundle = make_bundle(70)
    folds = make_folds(bundle)[:2]
    targets = {
        "cash": {fold.fold_id: empty_targets() for fold in folds}
    }
    run = run_walk_forward_experiments(bundle, folds, targets)
    summary = run.summaries["cash"]
    assert summary["fold_count"] == 2
    assert summary["mean_fold_return"] == pytest.approx(0.0)
    assert "not concatenated or compounded" in summary["aggregation_note"]


def test_runner_rejects_non_finite_target_weights():
    bundle = make_bundle(70)
    fold = make_folds(bundle)[0]
    targets = pl.DataFrame({
        "signal_date": [fold.test_start],
        "instrument": ["600000.SH"],
        "weight": [float("nan")],
    })
    with pytest.raises(ResearchBundleError, match="finite"):
        run_walk_forward_experiments(
            bundle, [fold], {"strategy": {fold.fold_id: targets}},
            backtest_config=BacktestConfig(top_n=1),
        )
