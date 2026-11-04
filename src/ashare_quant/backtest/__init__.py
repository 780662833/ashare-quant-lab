"""Deterministic A-share backtesting primitives."""

from ashare_quant.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    CostModel,
    equal_weight_targets,
    run_backtest,
    weekly_rebalance_dates,
)
from ashare_quant.backtest.report import summarize_backtest

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "CostModel",
    "equal_weight_targets",
    "run_backtest",
    "summarize_backtest",
    "weekly_rebalance_dates",
]
