"""Walk-forward execution runner for the deterministic momentum benchmark.

The runner replays each fold only through that fold's test-end date. Momentum
is rule-based and does not fit a model; this module does not create labels,
train/tune models, or unlock model training.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import prod
from statistics import mean, median
from typing import Any

import polars as pl

from ashare_quant.backtest import BacktestConfig, BacktestResult, summarize_backtest
from ashare_quant.research.benchmark import run_momentum_benchmark
from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)
from ashare_quant.strategies.momentum import MomentumConfig


@dataclass(frozen=True, slots=True)
class WalkForwardMomentumFoldRun:
    """One fold's test-only outputs and its immediately preceding NAV baseline."""

    fold: WalkForwardFold
    baseline_date: str
    baseline_nav: float
    daily: pl.DataFrame
    orders: pl.DataFrame
    holdings: pl.DataFrame
    report: dict[str, Any]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly fold manifest without embedding DataFrames."""
        return {
            "fold": asdict(self.fold),
            "baseline_date": self.baseline_date,
            "baseline_nav": self.baseline_nav,
            "report": self.report,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class WalkForwardMomentumRun:
    """Fold results, concatenated out-of-sample ledgers and aggregate diagnostics."""

    folds: list[WalkForwardMomentumFoldRun]
    fold_table: pl.DataFrame
    oos_daily: pl.DataFrame
    oos_orders: pl.DataFrame
    oos_holdings: pl.DataFrame
    aggregate_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the summary manifest; detailed ledgers remain separate frames."""
        return {
            "folds": [fold.to_dict() for fold in self.folds],
            "aggregate_report": self.aggregate_report,
        }


def _date_frame(frame: pl.DataFrame, cutoff: str) -> pl.DataFrame:
    if "date" not in frame.columns:
        raise ValueError("Canonical bundle frame is missing its date column")
    return frame.filter(pl.col("date").cast(pl.String) <= cutoff)


def _prefix_bundle(
    bundle: ResearchDataBundle,
    *,
    fold: WalkForwardFold,
    prefix_sessions: list[str],
) -> ResearchDataBundle:
    """Create a cutoff-bound view so the fold cannot read later observations."""
    cutoff = fold.test_end
    provenance = {
        **bundle.provenance,
        "walk_forward": {
            "fold_id": fold.fold_id,
            "data_cutoff_date": cutoff,
            "feature_data_cutoff_date": cutoff,
            "execution_data_cutoff_date": cutoff,
            "training_dataset_unlocked": False,
            "model_training_performed": False,
        },
    }
    return ResearchDataBundle(
        execution_bars=_date_frame(bundle.execution_bars, cutoff),
        adjusted_bars=_date_frame(bundle.adjusted_bars, cutoff),
        universe_membership=_date_frame(bundle.universe_membership, cutoff),
        trading_dates=prefix_sessions,
        provenance=provenance,
    )


def _fold_report(
    result: BacktestResult,
    *,
    test_start: str,
    test_end: str,
    baseline_nav: float,
    annualization: int,
) -> dict[str, Any]:
    """Summarize test returns using the prior session's close as initial NAV.

    The first test-session daily return is measured from the prior session's
    close. To retain that return and the correct total-return denominator, a
    private copy of the first test NAV is replaced with baseline_nav before
    calling the common report layer. The returned daily ledger keeps actual NAV.
    """
    test_daily = result.daily.sort("date")
    if test_daily.is_empty():
        raise ValueError("Walk-forward fold has no test-period NAV rows")
    if str(test_daily["date"][0]) != test_start or str(test_daily["date"][-1]) != test_end:
        raise ValueError(
            f"Fold NAV coverage does not match its test window {test_start}..{test_end}"
        )

    metric_daily = test_daily.with_columns(
        pl.when(pl.col("date").cast(pl.String) == test_start)
        .then(pl.lit(float(baseline_nav)))
        .otherwise(pl.col("nav"))
        .alias("nav")
    )
    report = summarize_backtest(
        BacktestResult(daily=metric_daily, orders=result.orders, holdings=result.holdings),
        annualization=annualization,
    )
    sessions = test_daily.height
    total_return = float(report["total_return"])
    gross_growth = 1.0 + total_return
    report["cagr"] = gross_growth ** (annualization / sessions) - 1.0
    report["period"] = {
        "start": test_start,
        "end": test_end,
        "sessions": sessions,
    }
    report["initial_nav"] = float(baseline_nav)
    return report


def _aggregate(
    folds: list[WalkForwardMomentumFoldRun],
    *,
    trading_dates: list[str],
) -> dict[str, Any]:
    returns = [float(item.report["total_return"]) for item in folds]
    fold_cagrs = [float(item.report["cagr"]) for item in folds]
    fold_sharpes = [float(item.report["sharpe_ratio"]) for item in folds]
    fold_drawdowns = [float(item.report["max_drawdown"]) for item in folds]
    total_sessions = sum(item.fold.test_sessions for item in folds)
    index = {session: position for position, session in enumerate(trading_dates)}
    contiguous = all(
        index[current.fold.test_start] == index[previous.fold.test_end] + 1
        for previous, current in zip(folds, folds[1:])
    )

    costs = {
        key: sum(float(item.report["costs"][key]) for item in folds)
        for key in (
            "commission", "transfer_fee", "stamp_duty", "slippage_cost",
            "total_fees", "total_transaction_costs",
        )
    }
    execution = {
        key: sum(int(item.report["execution"][key]) for item in folds)
        for key in ("order_count", "filled_order_count", "no_fill_order_count")
    }
    linked_return = prod(1.0 + value for value in returns) - 1.0
    return {
        "fold_count": len(folds),
        "out_of_sample_sessions": total_sessions,
        "test_windows_contiguous": contiguous,
        "mean_fold_total_return": mean(returns),
        "median_fold_total_return": median(returns),
        "positive_fold_ratio": sum(value > 0 for value in returns) / len(returns),
        "mean_fold_cagr": mean(fold_cagrs),
        "mean_fold_sharpe": mean(fold_sharpes),
        "worst_fold_max_drawdown": min(fold_drawdowns),
        "fold_linked_total_return": linked_return,
        "fold_linked_cagr": (
            (1.0 + linked_return) ** (252 / total_sessions) - 1.0
            if contiguous and total_sessions > 0 else None
        ),
        "execution": execution,
        "costs": costs,
        "interpretation": (
            "Test windows are contiguous; linked return compounds the same "
            "continuous rule-based replay across the out-of-sample sessions."
            if contiguous else
            "Test windows contain gaps. Fold-linked return compounds only the "
            "reported test windows and excludes intervening sessions; it is "
            "not a continuous calendar-period portfolio return."
        ),
        "model_training_performed": False,
        "training_dataset_unlocked": False,
    }


def run_walk_forward_momentum(
    bundle: ResearchDataBundle,
    *,
    walk_forward_config: WalkForwardConfig | None = None,
    strategy_config: MomentumConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    annualization: int = 252,
) -> WalkForwardMomentumRun:
    """Run the same momentum rule through chronological test folds.

    For every fold, source panels are truncated at test_end before momentum
    scoring/backtesting. The full history up to that cutoff is replayed so the
    deterministic benchmark enters the test window with the holdings/cash it
    would have accumulated by then. The NAV at the immediately preceding
    exchange session is the fold's return baseline.

    Only test-window rows are returned in each fold's daily/order/holding
    ledgers. The prior session is used only as the NAV denominator. No model is
    fitted, no future rows are passed to the fold run, and no training gate is
    changed.
    """
    if annualization < 1:
        raise ValueError("annualization must be positive")
    if bundle.provenance.get("training_dataset_unlocked") is not False:
        raise ValueError("Walk-forward benchmark requires training_dataset_unlocked=false")

    sessions = list(bundle.trading_dates)
    config = walk_forward_config or WalkForwardConfig()
    strategy = strategy_config or MomentumConfig()
    execution = backtest_config or BacktestConfig(top_n=strategy.top_n)
    if execution.top_n != strategy.top_n:
        raise ValueError("MomentumConfig.top_n and BacktestConfig.top_n must match")

    planned = build_walk_forward_folds(sessions, config)
    position = {session: index for index, session in enumerate(sessions)}
    fold_runs: list[WalkForwardMomentumFoldRun] = []

    for fold in planned:
        test_start_index = position[fold.test_start]
        if test_start_index < 1:
            raise ValueError(f"{fold.fold_id} has no preceding NAV baseline session")
        baseline_date = sessions[test_start_index - 1]
        prefix_sessions = sessions[: position[fold.test_end] + 1]
        prefix = _prefix_bundle(bundle, fold=fold, prefix_sessions=prefix_sessions)

        # Enforce the cutoff at every panel boundary before the strategy sees it.
        for name, frame in (
            ("execution_bars", prefix.execution_bars),
            ("adjusted_bars", prefix.adjusted_bars),
            ("universe_membership", prefix.universe_membership),
        ):
            if frame.filter(pl.col("date").cast(pl.String) > fold.test_end).height:
                raise AssertionError(f"{name} contains observations after fold cutoff")

        benchmark = run_momentum_benchmark(
            prefix,
            strategy_config=strategy,
            backtest_config=execution,
        )
        full_daily = benchmark.result.daily.sort("date")
        baseline = full_daily.filter(pl.col("date").cast(pl.String) == baseline_date)
        if baseline.height != 1:
            raise ValueError(
                f"{fold.fold_id} is missing the preceding-session NAV baseline {baseline_date}"
            )
        test_daily = full_daily.filter(
            pl.col("date").cast(pl.String).is_between(
                fold.test_start, fold.test_end, closed="both"
            )
        )
        if test_daily.height != fold.test_sessions:
            raise ValueError(
                f"{fold.fold_id} expected {fold.test_sessions} NAV sessions, "
                f"received {test_daily.height}"
            )
        test_orders = benchmark.result.orders.filter(
            pl.col("date").cast(pl.String).is_between(
                fold.test_start, fold.test_end, closed="both"
            )
        )
        test_holdings = benchmark.result.holdings.filter(
            pl.col("date").cast(pl.String).is_between(
                fold.test_start, fold.test_end, closed="both"
            )
        )
        baseline_nav = float(baseline["nav"].item())
        report = _fold_report(
            BacktestResult(
                daily=test_daily,
                orders=test_orders,
                holdings=test_holdings,
            ),
            test_start=fold.test_start,
            test_end=fold.test_end,
            baseline_nav=baseline_nav,
            annualization=annualization,
        )
        evaluation_provenance = {
            **benchmark.provenance,
            "walk_forward": {
                "fold": asdict(fold),
                "baseline_date": baseline_date,
                "baseline_nav": baseline_nav,
                "data_cutoff_date": fold.test_end,
                "feature_data_cutoff_date": fold.test_end,
                "execution_data_cutoff_date": fold.test_end,
                "pit_fundamentals_used": False,
                "model_training_performed": False,
                "training_dataset_unlocked": False,
            },
        }
        fold_runs.append(
            WalkForwardMomentumFoldRun(
                fold=fold,
                baseline_date=baseline_date,
                baseline_nav=baseline_nav,
                daily=test_daily,
                orders=test_orders,
                holdings=test_holdings,
                report=report,
                provenance=evaluation_provenance,
            )
        )

    fold_rows = [
        {
            "fold_id": item.fold.fold_id,
            "test_start": item.fold.test_start,
            "test_end": item.fold.test_end,
            "test_sessions": item.fold.test_sessions,
            "baseline_date": item.baseline_date,
            "baseline_nav": item.baseline_nav,
            "total_return": item.report["total_return"],
            "cagr": item.report["cagr"],
            "annualized_volatility": item.report["annualized_volatility"],
            "sharpe_ratio": item.report["sharpe_ratio"],
            "max_drawdown": item.report["max_drawdown"],
            "order_count": item.report["execution"]["order_count"],
            "no_fill_order_count": item.report["execution"]["no_fill_order_count"],
            "total_transaction_costs": item.report["costs"]["total_transaction_costs"],
        }
        for item in fold_runs
    ]
    fold_table = pl.DataFrame(fold_rows).sort("fold_id")
    oos_daily = pl.concat([item.daily for item in fold_runs], how="vertical_relaxed").sort("date")
    oos_orders = pl.concat([item.orders for item in fold_runs], how="vertical_relaxed").sort(
        ["date", "instrument"]
    )
    oos_holdings = pl.concat([item.holdings for item in fold_runs], how="vertical_relaxed").sort(
        ["date", "instrument"]
    )
    return WalkForwardMomentumRun(
        folds=fold_runs,
        fold_table=fold_table,
        oos_daily=oos_daily,
        oos_orders=oos_orders,
        oos_holdings=oos_holdings,
        aggregate_report=_aggregate(fold_runs, trading_dates=sessions),
    )
