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
            "high": 13.5,
            "low": 9.5,
            "close": 10.0 + offset,
            "volume": 100000,
            "amount": 1000000.0,
            "is_suspended": offset in (suspended or set()),
            "is_limit_up_locked": offset == locked_buy_day,
            "is_limit_down_locked": False,
            "limit_up": 14.0,
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
    assert result.orders["target_signal_date"].to_list() == ["2024-01-01", "2024-01-01"]
    assert result.orders["target_requested_qty"].to_list() == [5000, 5000]


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



def test_missing_bar_for_new_target_is_recorded_as_no_fill():
    rows = make_bars(days=3).to_dicts()
    rows = [row for row in rows if row["date"] != "2024-01-02"]
    # Keep the session in the calendar through a second instrument.
    for offset in range(3):
        day = (date(2024, 1, 1) + timedelta(days=offset)).isoformat()
        if day == "2024-01-02":
            row = make_bars(days=3).filter(pl.col("date") == day).to_dicts()[0]
            row["instrument"] = "000001.SZ"
            rows.append(row)
    bars = pl.DataFrame(rows)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.5],
    })
    result = run_backtest(bars, targets, BacktestConfig(initial_cash=100000))
    missing_attempt = result.orders.filter(pl.col("reason") == "missing_bar")
    assert missing_attempt.height == 1
    assert missing_attempt["status"].item() == "no_fill"


def test_configured_top_n_is_enforced():
    bars = make_bars()
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01", "2024-01-01"],
        "instrument": ["600000.SH", "000001.SZ"],
        "weight": [0.25, 0.25],
    })
    with pytest.raises(ValueError, match="top_n"):
        run_backtest(bars, targets, BacktestConfig(initial_cash=100000, top_n=1))



def test_filled_target_is_not_rebalanced_every_day_as_price_moves():
    rows = make_bars(days=3).to_dicts()
    rows[2]["open"] = 12.0
    rows[2]["high"] = 13.5
    rows[2]["close"] = 12.5
    bars = pl.DataFrame(rows)
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
    assert result.orders["filled_qty"].to_list() == [5000]


def test_order_ledger_separates_fee_components_and_slippage_without_double_charging():
    bars = make_bars(days=4)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01", "2024-01-02"],
        "instrument": ["600000.SH", "600000.SH"],
        "weight": [0.5, 0.0],
    })
    costs = CostModel(
        commission_rate=0.001,
        minimum_commission=2.0,
        stamp_duty_sell_rate=0.001,
        transfer_fee_rate=0.0002,
        slippage_bps=100.0,
    )
    result = run_backtest(
        bars, targets,
        BacktestConfig(initial_cash=100000, top_n=1, costs=costs),
    )
    assert result.orders["side"].to_list() == ["buy", "sell"]
    buy, sell = result.orders.row(0, named=True), result.orders.row(1, named=True)

    assert buy["filled_qty"] == 5000
    assert buy["fill_price"] == pytest.approx(10.10)
    assert buy["commission"] == pytest.approx(50.5)
    assert buy["transfer_fee"] == pytest.approx(10.1)
    assert buy["stamp_duty"] == pytest.approx(0.0)
    assert buy["slippage_cost"] == pytest.approx(500.0)
    assert buy["fees"] == pytest.approx(60.6)

    assert sell["filled_qty"] == 5000
    assert sell["fill_price"] == pytest.approx(9.90)
    assert sell["commission"] == pytest.approx(49.5)
    assert sell["transfer_fee"] == pytest.approx(9.9)
    assert sell["stamp_duty"] == pytest.approx(49.5)
    assert sell["slippage_cost"] == pytest.approx(500.0)
    assert sell["fees"] == pytest.approx(108.9)

    # Slippage is embedded in fill_price/cash accounting; only fees are
    # deducted as a separate cash item. This prevents double subtraction.
    assert result.daily["cash"].min() >= 0
    assert result.orders["slippage_cost"].sum() == pytest.approx(1000.0)
    assert result.orders["fees"].sum() == pytest.approx(169.5)



def test_new_signal_recomputes_existing_holding_target_quantity():
    bars = make_bars(days=4)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01", "2024-01-02"],
        "instrument": ["600000.SH", "600000.SH"],
        "weight": [0.5, 0.25],
    })
    zero_costs = CostModel(
        commission_rate=0, minimum_commission=0,
        stamp_duty_sell_rate=0, transfer_fee_rate=0, slippage_bps=0,
    )

    result = run_backtest(
        bars, targets,
        BacktestConfig(initial_cash=100000, costs=zero_costs),
    )

    # First signal buys 5,000 shares. The second signal snapshots NAV at
    # 105,000, so its 25% target at the next open is 2,600 board-lot shares.
    assert result.orders["date"].to_list() == ["2024-01-02", "2024-01-03"]
    assert result.orders["side"].to_list() == ["buy", "sell"]
    assert result.orders["filled_qty"].to_list() == [5000, 2400]
    assert result.holdings.filter(pl.col("date") == "2024-01-03")["quantity"].item() == 2600

 
 
def test_target_fill_ratio_caps_cumulative_buy_quantity_across_retries():
    bars = make_bars(days=4, locked_buy_day=1)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.5],
    })
    zero_costs = CostModel(
        commission_rate=0, minimum_commission=0,
        stamp_duty_sell_rate=0, transfer_fee_rate=0, slippage_bps=0,
    )
    result = run_backtest(
        bars, targets,
        BacktestConfig(
            initial_cash=100000, costs=zero_costs, target_fill_ratio=0.5,
        ),
    )
    assert result.orders["status"].to_list() == ["no_fill", "filled"]
    assert result.orders["requested_qty"].to_list() == [2500, 2500]
    assert result.orders["filled_qty"].to_list() == [0, 2500]
    assert result.orders["target_requested_qty"].to_list() == [5000, 5000]
    assert result.orders["target_fill_cap_qty"].to_list() == [2500, 2500]
    assert result.holdings.filter(pl.col("date") == "2024-01-03")["quantity"].item() == 2500


def test_target_fill_ratio_caps_sell_quantity_without_retries_beyond_cap():
    bars = make_bars(days=4)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01", "2024-01-02"],
        "instrument": ["600000.SH", "600000.SH"],
        "weight": [0.5, 0.0],
    })
    zero_costs = CostModel(
        commission_rate=0, minimum_commission=0,
        stamp_duty_sell_rate=0, transfer_fee_rate=0, slippage_bps=0,
    )
    result = run_backtest(
        bars, targets,
        BacktestConfig(
            initial_cash=100000, costs=zero_costs, target_fill_ratio=0.5,
        ),
    )
    assert result.orders["side"].to_list() == ["buy", "sell"]
    assert result.orders["target_requested_qty"].to_list() == [5000, 2500]
    assert result.orders["target_fill_cap_qty"].to_list() == [2500, 1200]
    assert result.orders["filled_qty"].to_list() == [2500, 1200]
    assert result.holdings.filter(pl.col("date") == "2024-01-04")["quantity"].item() == 1300


def test_target_fill_ratio_below_one_board_lot_is_explicit_no_fill():
    bars = make_bars(days=3)
    targets = pl.DataFrame({
        "signal_date": ["2024-01-01"],
        "instrument": ["600000.SH"],
        "weight": [0.01],
    })
    result = run_backtest(
        bars, targets,
        BacktestConfig(initial_cash=100000, target_fill_ratio=0.5),
    )
    assert result.orders.height == 1
    assert result.orders["target_requested_qty"].item() == 100
    assert result.orders["target_fill_cap_qty"].item() == 0
    assert result.orders["requested_qty"].item() == 0
    assert result.orders["filled_qty"].item() == 0
    assert result.orders["reason"].item() == "target_fill_cap_below_board_lot"


@pytest.mark.parametrize("ratio", [-0.01, 1.01])
def test_target_fill_ratio_must_be_between_zero_and_one(ratio):
    with pytest.raises(ValueError, match="target_fill_ratio"):
        BacktestConfig(target_fill_ratio=ratio)
