"""Auditable performance and execution-cost summaries for backtest results."""
from __future__ import annotations

from math import sqrt
from typing import Any

import polars as pl

from ashare_quant.backtest.engine import BacktestResult


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _performance(nav: pl.Series, daily_returns: pl.Series, annualization: int, risk_free_rate: float) -> dict[str, float]:
    values = [_safe_float(value) for value in nav.to_list()]
    if not values or values[0] <= 0:
        raise ValueError("NAV must contain positive values")
    if any(value <= 0 for value in values):
        raise ValueError("NAV must remain positive for performance reporting")

    periods = max(len(values) - 1, 0)
    total_return = values[-1] / values[0] - 1.0
    cagr = (values[-1] / values[0]) ** (annualization / periods) - 1.0 if periods else 0.0

    returns = [_safe_float(value) for value in daily_returns.to_list()]
    if len(returns) > 1:
        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
        volatility = sqrt(max(variance, 0.0)) * sqrt(annualization)
    else:
        volatility = 0.0
    daily_rf = risk_free_rate / annualization
    excess = [value - daily_rf for value in returns]
    if len(excess) > 1:
        mean_excess = sum(excess) / len(excess)
        variance_excess = sum((value - mean_excess) ** 2 for value in excess) / (len(excess) - 1)
        daily_std = sqrt(max(variance_excess, 0.0))
        sharpe = mean_excess / daily_std * sqrt(annualization) if daily_std else 0.0
    else:
        sharpe = 0.0

    peak = values[0]
    drawdowns: list[float] = []
    for value in values:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1.0)
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "max_drawdown": min(drawdowns),
        "sharpe_ratio": sharpe,
    }


def summarize_backtest(
    result: BacktestResult,
    benchmark_daily: pl.DataFrame | None = None,
    *,
    annualization: int = 252,
    risk_free_rate: float = 0.0,
) -> dict[str, Any]:
    """Summarize NAV, drawdown, execution, costs, turnover, and optional benchmark.

    Benchmark input must contain date and nav and is aligned to the strategy's
    observed dates. Excess return is the difference in cumulative returns over
    the common first/last dates, not a claim of statistical significance.
    """
    if annualization < 1:
        raise ValueError("annualization must be positive")
    if risk_free_rate < 0:
        raise ValueError("risk_free_rate must be non-negative")
    daily = result.daily.sort("date")
    required = {"date", "nav", "daily_return"}
    missing = required.difference(daily.columns)
    if missing or daily.is_empty():
        raise ValueError(f"Daily result missing required columns or empty: {sorted(missing)}")
    metrics = _performance(daily["nav"], daily["daily_return"], annualization, risk_free_rate)

    orders = result.orders
    def order_cost(column: str) -> float:
        # Backward compatible with hand-built/legacy BacktestResult fixtures.
        if not orders.height or column not in orders.columns:
            return 0.0
        return _safe_float(orders[column].sum())

    commission = order_cost("commission")
    transfer_fee = order_cost("transfer_fee")
    stamp_duty = order_cost("stamp_duty")
    total_fees = order_cost("fees")
    slippage_cost = order_cost("slippage_cost")
    total_transaction_costs = total_fees + slippage_cost
    if orders.height and {"filled_qty", "fill_price"}.issubset(orders.columns):
        notionals = orders.with_columns(
            (pl.col("filled_qty") * pl.col("fill_price").fill_null(0)).alias("_notional")
        )
        traded_notional = _safe_float(notionals["_notional"].sum())
    else:
        traded_notional = 0.0
    average_nav = _safe_float(daily["nav"].mean())
    turnover = traded_notional / average_nav if average_nav > 0 else 0.0
    filled_orders = orders.filter(pl.col("filled_qty") > 0).height if orders.height and "filled_qty" in orders.columns else 0
    no_fill_orders = orders.filter(pl.col("filled_qty") == 0).height if orders.height and "filled_qty" in orders.columns else 0
    requested_qty = _safe_float(orders["requested_qty"].sum()) if orders.height and "requested_qty" in orders.columns else 0.0
    filled_qty = _safe_float(orders["filled_qty"].sum()) if orders.height and "filled_qty" in orders.columns else 0.0

    first_date = str(daily["date"][0])
    last_date = str(daily["date"][-1])
    initial_nav = _safe_float(daily["nav"][0])
    final_nav = _safe_float(daily["nav"][-1])
    output: dict[str, Any] = {
        "period": {"start": first_date, "end": last_date, "sessions": daily.height},
        "initial_nav": initial_nav,
        "final_nav": final_nav,
        **metrics,
        "execution": {
            "order_count": orders.height,
            "filled_order_count": filled_orders,
            "no_fill_order_count": no_fill_orders,
            "share_fill_ratio": filled_qty / requested_qty if requested_qty > 0 else 0.0,
            "traded_notional": traded_notional,
            "turnover": turnover,
        },
        "costs": {
            "commission": commission,
            "transfer_fee": transfer_fee,
            "stamp_duty": stamp_duty,
            "slippage_cost": slippage_cost,
            "total_fees": total_fees,
            "total_transaction_costs": total_transaction_costs,
            "fees_bps_of_initial_nav": (
                total_fees / initial_nav * 10_000 if initial_nav > 0 else 0.0
            ),
            "total_costs_bps_of_initial_nav": (
                total_transaction_costs / initial_nav * 10_000 if initial_nav > 0 else 0.0
            ),
        },
        "benchmark": None,
    }

    if benchmark_daily is not None:
        benchmark_missing = {"date", "nav"}.difference(benchmark_daily.columns)
        if benchmark_missing:
            raise ValueError(f"Benchmark missing columns: {sorted(benchmark_missing)}")
        benchmark = benchmark_daily.select(
            pl.col("date").cast(pl.String),
            pl.col("nav").cast(pl.Float64).alias("benchmark_nav"),
        )
        strategy_dates = daily.select(pl.col("date").cast(pl.String), pl.col("nav").alias("strategy_nav"))
        common = strategy_dates.join(benchmark, on="date", how="inner").sort("date")
        if common.height < 2:
            raise ValueError("Benchmark must overlap strategy on at least two sessions")
        benchmark_return = _safe_float(common["benchmark_nav"][-1]) / _safe_float(common["benchmark_nav"][0]) - 1.0
        strategy_return = _safe_float(common["strategy_nav"][-1]) / _safe_float(common["strategy_nav"][0]) - 1.0
        output["benchmark"] = {
            "start": str(common["date"][0]),
            "end": str(common["date"][-1]),
            "sessions": common.height,
            "total_return": benchmark_return,
            "strategy_total_return_same_window": strategy_return,
            "excess_return": strategy_return - benchmark_return,
        }
    return output
