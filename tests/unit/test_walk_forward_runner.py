from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.backtest import BacktestConfig, CostModel
from ashare_quant.backtest.engine import BacktestResult
from ashare_quant.research import (
    ResearchDataBundle,
    WalkForwardConfig,
    build_research_data_bundle,
    run_walk_forward_momentum,
)
from ashare_quant.research.walk_forward_runner import _fold_report
from ashare_quant.strategies.momentum import MomentumConfig


PROFILE = "cn_a_sh_sz_research_v1"


def make_sessions(count: int) -> list[str]:
    sessions = []
    current = date(2020, 1, 1)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return sessions


def make_bundle(count: int = 280) -> ResearchDataBundle:
    sessions = make_sessions(count)
    raw_rows = []
    adjusted_rows = []
    status_rows = []
    membership_rows = []
    for index, session in enumerate(sessions):
        for symbol, base, slope in (
            ("600000.SH", 10.0, 0.035),
            ("000001.SZ", 18.0, 0.012),
            ("600519.SH", 30.0, 0.045),
        ):
            close = base + slope * index + 0.15 * ((index % 7) - 3)
            raw_rows.append({
                "instrument": symbol, "date": session,
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "volume": 100_000.0, "amount": close * 100_000.0,
            })
            adjusted_rows.append({
                "instrument": symbol, "date": session,
                "adj_close": close, "adj_is_exact": True,
            })
            status_rows.append({
                "instrument": symbol, "date": session,
                "is_suspended": False, "is_limit_up_locked": False,
                "is_limit_down_locked": False,
                "limit_up": close * 1.20, "limit_down": close * 0.80,
                "risk_warning_known": True,
                "limit_evidence_source": "synthetic-test-provider",
                "limit_rule_version": "synthetic-v1",
                "limit_regime": "synthetic-main-board",
            })

    final_by_week: dict[tuple[int, int], str] = {}
    for session in sessions:
        iso = date.fromisoformat(session).isocalendar()
        final_by_week[(iso.year, iso.week)] = session
    for session in final_by_week.values():
        for symbol in ("600000.SH", "000001.SZ", "600519.SH"):
            membership_rows.append({"instrument": symbol, "date": session})

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
            "historical_universe": {"replayable": True, "revision": "universe-v1"},
            "hfq": {"replayable": True, "revision": "hfq-v1"},
        },
    }
    revisions = {
        "daily_bars": "daily-v1",
        "adj_factors": "hfq-v1",
        "universe": "universe-v1",
        "trading_status": "status-v1",
        "trading_calendar": "calendar-v1",
        "price_limit_rules": "synthetic-v1",
    }
    return build_research_data_bundle(
        raw_bars=pl.DataFrame(raw_rows),
        adjusted_bars=pl.DataFrame(adjusted_rows),
        universe_membership=pl.DataFrame(membership_rows),
        execution_evidence=pl.DataFrame(status_rows),
        trading_dates=sessions,
        acceptance_report=report,
        acceptance_report_id="synthetic-acceptance-v1",
        source_revisions=revisions,
        start_date=sessions[0],
        end_date=sessions[-1],
        universe_profile=PROFILE,
    )


@pytest.fixture
def configs():
    return {
        "walk_forward_config": WalkForwardConfig(
            min_train_sessions=100,
            validation_sessions=20,
            test_sessions=20,
            label_horizon_sessions=3,
            embargo_sessions=1,
        ),
        "strategy_config": MomentumConfig(
            top_n=2, lookback_sessions=30, skip_recent_sessions=5
        ),
        "backtest_config": BacktestConfig(
            initial_cash=100_000,
            top_n=2,
            costs=CostModel(
                commission_rate=0.0003,
                minimum_commission=2.0,
                stamp_duty_sell_rate=0.0005,
                transfer_fee_rate=0.00001,
                slippage_bps=5.0,
            ),
        ),
    }


def test_walk_forward_runner_returns_fold_metrics_and_test_only_ledgers(configs):
    bundle = make_bundle()
    run = run_walk_forward_momentum(bundle, **configs)

    assert len(run.folds) >= 3
    assert run.fold_table.height == len(run.folds)
    assert run.aggregate_report["test_windows_contiguous"] is True
    assert run.aggregate_report["model_training_performed"] is False
    assert run.aggregate_report["training_dataset_unlocked"] is False

    for fold_run in run.folds:
        fold = fold_run.fold
        assert fold_run.daily["date"][0] == fold.test_start
        assert fold_run.daily["date"][-1] == fold.test_end
        assert fold_run.daily.height == fold.test_sessions
        assert fold_run.orders.filter(
            pl.col("date").cast(pl.String) < fold.test_start
        ).is_empty()
        assert fold_run.orders.filter(
            pl.col("date").cast(pl.String) > fold.test_end
        ).is_empty()
        assert fold_run.baseline_date < fold.test_start
        assert fold_run.report["period"]["sessions"] == fold.test_sessions
        assert fold_run.report["initial_nav"] == pytest.approx(fold_run.baseline_nav)
        assert fold_run.provenance["walk_forward"]["data_cutoff_date"] == fold.test_end
        assert fold_run.provenance["walk_forward"]["training_dataset_unlocked"] is False

    assert run.oos_daily.height == sum(item.fold.test_sessions for item in run.folds)
    assert run.fold_table["total_return"].is_finite().all()
    assert run.aggregate_report["execution"]["order_count"] == run.oos_orders.height


def test_each_fold_is_truncated_at_its_own_test_end(configs):
    original = make_bundle()
    first = run_walk_forward_momentum(original, **configs).folds[0]

    changed_adjusted = original.adjusted_bars.with_columns(
        pl.when(pl.col("date") > first.fold.test_end)
        .then(pl.col("adj_close") * 1000.0)
        .otherwise(pl.col("adj_close"))
        .alias("adj_close")
    )
    changed_bundle = ResearchDataBundle(
        execution_bars=original.execution_bars,
        adjusted_bars=changed_adjusted,
        universe_membership=original.universe_membership,
        trading_dates=original.trading_dates,
        provenance=original.provenance,
    )
    changed_first = run_walk_forward_momentum(changed_bundle, **configs).folds[0]

    assert changed_first.daily.equals(first.daily)
    assert changed_first.orders.equals(first.orders)
    assert changed_first.report == first.report


def test_runner_rejects_bundle_that_claims_training_is_unlocked(configs):
    bundle = make_bundle()
    invalid = ResearchDataBundle(
        execution_bars=bundle.execution_bars,
        adjusted_bars=bundle.adjusted_bars,
        universe_membership=bundle.universe_membership,
        trading_dates=bundle.trading_dates,
        provenance={**bundle.provenance, "training_dataset_unlocked": True},
    )
    with pytest.raises(ValueError, match="training_dataset_unlocked=false"):
        run_walk_forward_momentum(invalid, **configs)


def test_runner_rejects_strategy_and_engine_top_n_mismatch(configs):
    bundle = make_bundle()
    configs = dict(configs)
    configs["backtest_config"] = BacktestConfig(initial_cash=100_000, top_n=20)
    with pytest.raises(ValueError, match="must match"):
        run_walk_forward_momentum(bundle, **configs)


def test_runner_rejects_panels_outside_the_supplied_calendar(configs):
    bundle = make_bundle()
    shortened = ResearchDataBundle(
        execution_bars=bundle.execution_bars,
        adjusted_bars=bundle.adjusted_bars,
        universe_membership=bundle.universe_membership,
        trading_dates=bundle.trading_dates[1:],
        provenance=bundle.provenance,
    )
    with pytest.raises(ValueError, match="outside the supplied trading calendar"):
        run_walk_forward_momentum(shortened, **configs)


@pytest.mark.parametrize(
    ("nav_values", "daily_returns"),
    [
        ([90.0], [-0.10]),
        ([90.0, 95.0], [-0.10, (95.0 / 90.0) - 1.0]),
    ],
)
def test_fold_report_uses_prior_close_without_overwriting_first_test_nav(
    nav_values, daily_returns
):
    dates = ["2020-01-02", "2020-01-03"][: len(nav_values)]
    daily = pl.DataFrame({
        "date": dates,
        "nav": nav_values,
        "daily_return": daily_returns,
    })
    result = BacktestResult(
        daily=daily,
        orders=pl.DataFrame({
            "commission": [1.0],
            "transfer_fee": [0.2],
            "stamp_duty": [0.0],
            "fees": [1.2],
            "slippage_cost": [0.5],
        }),
        holdings=pl.DataFrame(),
    )

    report = _fold_report(
        result,
        test_start=dates[0],
        test_end=dates[-1],
        baseline_nav=100.0,
        annualization=252,
    )

    expected_return = nav_values[-1] / 100.0 - 1.0
    expected_cagr = (1.0 + expected_return) ** (252 / len(nav_values)) - 1.0
    assert report["period"]["sessions"] == len(nav_values)
    assert report["initial_nav"] == pytest.approx(100.0)
    assert report["final_nav"] == pytest.approx(nav_values[-1])
    assert report["total_return"] == pytest.approx(expected_return)
    assert report["cagr"] == pytest.approx(expected_cagr)
    assert report["costs"]["fees_bps_of_initial_nav"] == pytest.approx(120.0)
    assert report["costs"]["total_costs_bps_of_initial_nav"] == pytest.approx(170.0)
    # The first test close is below the baseline in both cases. It must count
    # toward drawdown, even when it is the only test observation.
    assert report["max_drawdown"] == pytest.approx(-0.10)
