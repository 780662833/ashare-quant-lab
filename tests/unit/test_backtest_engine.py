from datetime import date, timedelta

import polars as pl
import pytest

from ashare_quant.backtest import (
    BacktestConfig,
    CostModel,
    equal_weight_targets,
    run_backtest,
    weekly_rebalance_dates,
)


def make_bars(days=4, *, locked_buy_day=None, suspended=None):
    rows = []
    start = date(2024, 1, 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        rows.append({
            "instrument": "600000.SH",
            "date": day.isoformat(),
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0 + offset,
            "volume": 100000,
            "amount": 1000000.0,
            "is_suspended": offset in (suspended or set()),
            "is_limit_up_locked": offset == locked_buy_day,
            "is_limit_down_locked": False,
            "limit_up": 11.0,
            "limit_down": 9.0,
        })
    return pl.DataFrame(rows)


def test_signal_at_close_fills_no_earlier_than_next_open():
    bars = make_bars()
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.5],
    })
    zero_costs = CostModel(
        commission_rate=0, minimum_commission=0,
        stamp_duty_sell_rate=0, transfer_fee_rate=0, slippage_bps=0,
    )
    result = run_backtest(bars, targets, BacktestConfig(initial_cash=100000, costs=zero_costs))
    assert result.orders["date"].to_list() == ["2024-01-02"]
    assert result.orders["status"].to_list() == ["filled"]
    assert result.orders["filled_qty"].to_list() == [5000]


def test_limit_up_locked_buy_is_not_filled_and_is_retried():
    bars = make_bars(days=3, locked_buy_day=1)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.5],
    })
    result = run_backtest(bars, targets, BacktestConfig(initial_cash=100000))
    assert result.orders["status"].to_list() == ["no_fill", "filled"]
    assert result.orders["reason"].to_list()[0] == "limit_up_locked"
    assert result.orders["date"].to_list() == ["2024-01-02", "2024-01-03"]


def test_suspended_security_is_not_filled():
    bars = make_bars(days=3, suspended={1})
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.5],
    })
    result = run_backtest(bars, targets, BacktestConfig(initial_cash=100000))
    assert result.orders["status"].to_list() == ["no_fill", "filled"]
    assert result.orders["reason"].to_list()[0] == "suspended"


def test_engine_fails_closed_without_execution_evidence():
    bars = make_bars().drop("is_limit_up_locked")
    targets = pl.DataFrame(schema={
        "signal_date": pl.String, "instrument": pl.String, "weight": pl.Float64,
    })
    with pytest.raises(ValueError, match="Missing required execution"):
        run_backtest(bars, targets)


def test_target_weights_cannot_exceed_full_exposure():
    bars = make_bars()
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01", "2024-01-01"],
        "instrument": ["600000.SH", "000001.SZ"],
        "weight": [0.7, 0.4],
    })
    with pytest.raises(ValueError, match="100%"):
        run_backtest(bars, targets)


def test_equal_weight_selection_is_deterministic_on_ties():
    scores = pl.DataFrame({
        "signal_date": ["2024-01-01"] * 3,
        "instrument": ["600001.SH", "600000.SH", "000001.SZ"],
        "score": [1.0, 1.0, 0.5],
    })
    targets = equal_weight_targets(scores, top_n=2)
    assert targets["instrument"].to_list() == ["600000.SH", "600001.SH"]
    assert targets["weight"].to_list() == [0.5, 0.5]


def test_weekly_rebalance_uses_last_available_session_per_iso_week():
    dates = ["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-08"]
    assert weekly_rebalance_dates(dates) == ["2024-01-05", "2024-01-08"]


def test_no_leverage_and_costs_reduce_nav():
    bars = make_bars(days=3)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [1.0],
    })
    result = run_backtest(bars, targets, BacktestConfig(initial_cash=100000))
    assert result.daily["cash"].min() >= -1e-6
    assert result.daily["gross_exposure"].max() <= 1.0 + 1e-9
    assert result.orders["fees"].sum() > 0
