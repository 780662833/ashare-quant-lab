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
