import polars as pl
import pytest

from ashare_quant.backtest import BacktestResult
from ashare_quant.backtest.report import summarize_backtest


def make_result():
    daily = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "cash": [50.0, 45.0, 40.0, 42.0],
        "market_value": [50.0, 55.0, 60.0, 58.0],
        "nav": [100.0, 100.0, 100.0, 100.0],
        "daily_return": [0.0, 0.0, 0.0, 0.0],
        "gross_exposure": [0.5, 0.55, 0.6, 0.58],
    })
    orders = pl.DataFrame({
        "date": ["2024-01-03", "2024-01-04"],
        "instrument": ["600000.SH", "600000.SH"],
        "side": ["buy", "sell"],
        "requested_qty": [10, 5],
        "filled_qty": [10, 5],
        "fill_price": [10.0, 11.0],
        "fees": [5.0, 5.5],
        "status": ["filled", "filled"],
        "reason": [None, None],
    })
    return BacktestResult(daily=daily, orders=orders, holdings=pl.DataFrame())


def test_report_includes_performance_cost_and_execution_metrics():
    report = summarize_backtest(make_result())
    assert report["period"] == {"start": "2024-01-02", "end": "2024-01-05", "sessions": 4}
    assert report["total_return"] == pytest.approx(0.0)
    assert report["max_drawdown"] == pytest.approx(0.0)
    assert report["costs"]["total_fees"] == pytest.approx(10.5)
    assert report["execution"]["traded_notional"] == pytest.approx(155.0)
    assert report["execution"]["turnover"] == pytest.approx(1.55)
    assert report["execution"]["share_fill_ratio"] == pytest.approx(1.0)
    assert report["benchmark"] is None


def test_report_calculates_drawdown_and_benchmark_excess():
    result = make_result()
    daily = result.daily.with_columns(
        pl.Series("nav", [100.0, 110.0, 90.0, 105.0]),
        pl.Series("daily_return", [0.0, 0.1, -20 / 110, 15 / 90]),
    )
    result = BacktestResult(daily=daily, orders=result.orders, holdings=result.holdings)
    benchmark = pl.DataFrame({
        "date": daily["date"],
        "nav": [100.0, 105.0, 100.0, 102.0],
    })
    report = summarize_backtest(result, benchmark)
    assert report["max_drawdown"] == pytest.approx(90 / 110 - 1)
    assert report["benchmark"]["excess_return"] == pytest.approx(0.05 - 0.02)


def test_report_rejects_non_overlapping_benchmark():
    benchmark = pl.DataFrame({"date": ["2025-01-01", "2025-01-02"], "nav": [100.0, 101.0]})
    with pytest.raises(ValueError, match="overlap"):
        summarize_backtest(make_result(), benchmark)


def test_report_rejects_invalid_nav():
    result = make_result()
    daily = result.daily.with_columns(pl.Series("nav", [100.0, 0.0, 100.0, 100.0]))
    with pytest.raises(ValueError, match="positive"):
        summarize_backtest(BacktestResult(daily=daily, orders=result.orders, holdings=result.holdings))


def test_report_rejects_invalid_annualization():
    with pytest.raises(ValueError, match="annualization"):
        summarize_backtest(make_result(), annualization=0)


def test_report_attributes_fee_components_and_slippage_separately():
    result = make_result()
    orders = result.orders.with_columns(
        pl.Series("fees", [4.0, 9.0]),
        pl.Series("commission", [3.0, 4.0]),
        pl.Series("transfer_fee", [1.0, 2.0]),
        pl.Series("stamp_duty", [0.0, 3.0]),
        pl.Series("slippage_cost", [5.0, 6.0]),
    )
    result = BacktestResult(daily=result.daily, orders=orders, holdings=result.holdings)
    report = summarize_backtest(result)
    assert report["costs"]["commission"] == pytest.approx(7.0)
    assert report["costs"]["transfer_fee"] == pytest.approx(3.0)
    assert report["costs"]["stamp_duty"] == pytest.approx(3.0)
    assert report["costs"]["slippage_cost"] == pytest.approx(11.0)
    assert report["costs"]["total_fees"] == pytest.approx(13.0)
    assert report["costs"]["total_transaction_costs"] == pytest.approx(24.0)
    assert report["costs"]["total_costs_bps_of_initial_nav"] == pytest.approx(2400.0)


def test_report_rejects_inconsistent_fee_component_ledger():
    result = make_result()
    orders = result.orders.with_columns(
        pl.Series("fees", [5.0, 5.5]),
        pl.Series("commission", [3.0, 4.0]),
        pl.Series("transfer_fee", [1.0, 2.0]),
        pl.Series("stamp_duty", [0.0, 3.0]),
    )
    result = BacktestResult(daily=result.daily, orders=orders, holdings=result.holdings)
    with pytest.raises(ValueError, match="do not equal"):
        summarize_backtest(result)



def test_report_uses_unique_target_transition_denominator_not_retry_attempts():
    result = make_result()
    orders = pl.DataFrame({
        "date": ["2024-01-03", "2024-01-04"],
        "instrument": ["600000.SH", "600000.SH"],
        "target_signal_date": ["2024-01-02", "2024-01-02"],
        "target_requested_qty": [100, 100],
        "requested_qty": [100, 50],
        "filled_qty": [50, 50],
        "fill_price": [10.0, 10.0],
        "fees": [0.0, 0.0],
        "status": ["partially_filled", "filled"],
        "reason": [None, None],
    })
    report = summarize_backtest(BacktestResult(daily=result.daily, orders=orders, holdings=result.holdings))
    assert report["execution"]["share_fill_ratio"] == pytest.approx(1.0)
    assert report["execution"]["share_fill_ratio_basis"] == "target_transition"
    assert report["execution"]["target_requested_shares"] == 100
    assert report["execution"]["filled_shares"] == 100


def test_report_uses_full_target_transition_as_denominator_for_capped_fill():
    result = make_result()
    orders = pl.DataFrame({
        "date": ["2024-01-03"],
        "instrument": ["600000.SH"],
        "target_signal_date": ["2024-01-02"],
        "target_requested_qty": [5000],
        "target_fill_cap_qty": [2500],
        "target_fill_ratio": [0.5],
        "requested_qty": [2500],
        "filled_qty": [2500],
        "fill_price": [10.0],
        "fees": [0.0],
        "status": ["filled"],
        "reason": [None],
    })
    report = summarize_backtest(
        BacktestResult(daily=result.daily, orders=orders, holdings=result.holdings)
    )
    assert report["execution"]["share_fill_ratio"] == pytest.approx(0.5)
    assert report["execution"]["target_requested_shares"] == 5000
    assert report["execution"]["filled_shares"] == 2500


def test_report_exposes_pending_end_of_window_targets():
    result = make_result()
    pending = pl.DataFrame([{
        "signal_date": "2024-01-05",
        "instrument": "000001.SZ",
        "target_weight": 0.05,
        "current_quantity": 0,
        "desired_quantity": None,
        "execution_target_quantity": None,
        "reason": "no_next_session",
    }], schema={
        "signal_date": pl.String,
        "instrument": pl.String,
        "target_weight": pl.Float64,
        "current_quantity": pl.Int64,
        "desired_quantity": pl.Int64,
        "execution_target_quantity": pl.Int64,
        "reason": pl.String,
    })
    report = summarize_backtest(BacktestResult(
        daily=result.daily, orders=result.orders, holdings=result.holdings,
        pending_targets=pending,
    ))
    assert report["execution"]["pending_target_count"] == 1
    assert report["execution"]["pending_targets"][0]["reason"] == "no_next_session"
