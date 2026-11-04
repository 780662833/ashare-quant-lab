"""Walk-forward experiment execution over fixed, precomputed fold targets.

This module does not fit models or create labels. Model targets must be
generated independently per fold under the four-gate admission contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest import BacktestConfig, BacktestResult, run_backtest, summarize_backtest
from ashare_quant.backtest.engine import equal_weight_targets
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.research.walk_forward import WalkForwardFold
from ashare_quant.strategies.momentum import MomentumConfig, momentum_scores

TARGET_COLUMNS = {"signal_date", "instrument", "weight"}


@dataclass(frozen=True, slots=True)
class FoldStrategyResult:
    """One strategy's out-of-sample result for one walk-forward fold."""

    strategy_name: str
    fold: WalkForwardFold
    targets: pl.DataFrame
    result: BacktestResult
    report: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WalkForwardExperimentResult:
    """Fold results and non-compounded summary statistics per strategy."""

    fold_results: tuple[FoldStrategyResult, ...]
    summaries: dict[str, dict[str, Any]]
    manifest: dict[str, Any]


def _validate_folds(
    bundle: ResearchDataBundle,
    folds: list[WalkForwardFold],
) -> list[WalkForwardFold]:
    if not folds:
        raise ResearchBundleError("At least one walk-forward fold is required")
    sessions = bundle.trading_dates
    session_set = set(sessions)
    seen_ids: set[str] = set()
    previous_test_end: str | None = None
    for fold in folds:
        if not isinstance(fold, WalkForwardFold):
            raise ResearchBundleError("folds must contain WalkForwardFold values")
        if not fold.fold_id or fold.fold_id in seen_ids:
            raise ResearchBundleError("Fold IDs must be non-empty and unique")
        seen_ids.add(fold.fold_id)
        boundaries = [
            fold.train_start, fold.train_end, fold.train_label_maturity,
            fold.validation_start, fold.validation_end,
            fold.validation_label_maturity, fold.test_start, fold.test_end,
        ]
        if any(value not in session_set for value in boundaries):
            raise ResearchBundleError(
                f"{fold.fold_id} contains a boundary outside bundle trading calendar"
            )
        if not (
            fold.train_start <= fold.train_end
            < fold.train_label_maturity
            < fold.validation_start <= fold.validation_end
            < fold.validation_label_maturity
            < fold.test_start <= fold.test_end
        ):
            raise ResearchBundleError(f"{fold.fold_id} has invalid chronological boundaries")
        test_sessions = [
            value for value in sessions if fold.test_start <= value <= fold.test_end
        ]
        if len(test_sessions) != fold.test_sessions:
            raise ResearchBundleError(
                f"{fold.fold_id} test session count mismatch: "
                f"declared={fold.test_sessions}, actual={len(test_sessions)}"
            )
        if previous_test_end is not None and fold.test_start <= previous_test_end:
            raise ResearchBundleError("Walk-forward test windows must not overlap")
        previous_test_end = fold.test_end
    return list(folds)


def _validate_fold_targets(
    targets: pl.DataFrame,
    *,
    fold: WalkForwardFold,
    test_dates: list[str],
) -> pl.DataFrame:
    if not isinstance(targets, pl.DataFrame):
        raise ResearchBundleError(f"{fold.fold_id} targets must be a Polars DataFrame")
    missing = TARGET_COLUMNS.difference(targets.columns)
    if missing:
        raise ResearchBundleError(
            f"{fold.fold_id} targets missing columns: {sorted(missing)}"
        )
    if targets.is_empty():
        return pl.DataFrame(
            schema={
                "signal_date": pl.String,
                "instrument": pl.String,
                "weight": pl.Float64,
            }
        )
    frame = targets.select(
        pl.col("signal_date").cast(pl.String, strict=True),
        pl.col("instrument").cast(pl.String, strict=True),
        pl.col("weight").cast(pl.Float64, strict=True),
    )
    if frame.select(
        pl.any_horizontal(
            pl.col("signal_date").is_null(),
            pl.col("instrument").is_null(),
            pl.col("weight").is_null(),
        ).any()
    ).item():
        raise ResearchBundleError(f"{fold.fold_id} targets contain null values")
    if frame.select(
        pl.struct(["signal_date", "instrument"]).is_duplicated().any()
    ).item():
        raise ResearchBundleError(f"{fold.fold_id} targets contain duplicate keys")
    allowed_signal_dates = set(test_dates[:-1])
    outside = sorted(set(frame["signal_date"].to_list()).difference(allowed_signal_dates))
    if outside:
        raise ResearchBundleError(
            f"{fold.fold_id} targets contain signals outside executable test window: {outside}"
        )
    return frame.sort(["signal_date", "instrument"])


def _aggregate_fold_reports(
    strategy_name: str,
    fold_results: list[FoldStrategyResult],
) -> dict[str, Any]:
    returns = [float(item.report["total_return"]) for item in fold_results]
    cagr = [float(item.report["cagr"]) for item in fold_results]
    sharpes = [float(item.report["sharpe_ratio"]) for item in fold_results]
    drawdowns = [float(item.report["max_drawdown"]) for item in fold_results]
    fill_rates = [
        float(item.report["execution"]["share_fill_ratio"]) for item in fold_results
    ]
    total_fees = sum(float(item.report["costs"]["total_fees"]) for item in fold_results)
    slippage = sum(float(item.report["costs"]["slippage_cost"]) for item in fold_results)
    total_costs = sum(
        float(item.report["costs"]["total_transaction_costs"]) for item in fold_results
    )
    return {
        "strategy": strategy_name,
        "fold_count": len(fold_results),
        "positive_fold_count": sum(value > 0 for value in returns),
        "positive_fold_rate": (
            sum(value > 0 for value in returns) / len(returns) if returns else 0.0
        ),
        "mean_fold_return": sum(returns) / len(returns) if returns else 0.0,
        "median_fold_return": median(returns) if returns else 0.0,
        "worst_fold_return": min(returns) if returns else 0.0,
        "best_fold_return": max(returns) if returns else 0.0,
        "mean_fold_cagr": sum(cagr) / len(cagr) if cagr else 0.0,
        "mean_fold_sharpe": sum(sharpes) / len(sharpes) if sharpes else 0.0,
        "mean_fold_max_drawdown": sum(drawdowns) / len(drawdowns) if drawdowns else 0.0,
        "mean_fold_share_fill_ratio": (
            sum(fill_rates) / len(fill_rates) if fill_rates else 0.0
        ),
        "sum_transaction_fees": total_fees,
        "sum_slippage_cost": slippage,
        "sum_total_transaction_costs": total_costs,
        "aggregation_note": (
            "Fold runs are independent; NAV paths are not concatenated or "
            "compounded into a synthetic continuous portfolio."
        ),
    }


def run_walk_forward_experiments(
    bundle: ResearchDataBundle,
    folds: list[WalkForwardFold],
    strategy_targets: Mapping[str, Mapping[str, pl.DataFrame]],
    *,
    backtest_config: BacktestConfig | None = None,
) -> WalkForwardExperimentResult:
    """Run precomputed target sets over identical out-of-sample fold windows.

    strategy_targets is shaped as strategy_name -> fold_id -> target frame.
    Every strategy supplies one frame per fold. Target signal dates must be
    inside the test window and strictly before its final session, so the
    next-session execution attempt remains inside the test period.

    This function cannot prove how an externally supplied model target frame
    was trained. Future model targets must be produced fold-by-fold by a
    separately gated training/evaluation workflow.
    """
    checked_folds = _validate_folds(bundle, folds)
    if not isinstance(strategy_targets, Mapping) or not strategy_targets:
        raise ResearchBundleError("strategy_targets must be a non-empty mapping")
    strategy_names = sorted(strategy_targets)
    if any(not isinstance(name, str) or not name.strip() for name in strategy_names):
        raise ResearchBundleError("Strategy names must be non-empty strings")

    cfg = backtest_config or BacktestConfig()
    expected_ids = {fold.fold_id for fold in checked_folds}
    for strategy_name in strategy_names:
        per_fold = strategy_targets[strategy_name]
        if not isinstance(per_fold, Mapping):
            raise ResearchBundleError(f"{strategy_name} targets must be mapped by fold_id")
        supplied_ids = set(per_fold)
        if supplied_ids != expected_ids:
            raise ResearchBundleError(
                f"{strategy_name} fold target keys mismatch; "
                f"missing={sorted(expected_ids - supplied_ids)}, "
                f"extra={sorted(supplied_ids - expected_ids)}"
            )

    all_results: list[FoldStrategyResult] = []
    by_strategy: dict[str, list[FoldStrategyResult]] = {
        name: [] for name in strategy_names
    }
    for fold in checked_folds:
        test_dates = [
            value for value in bundle.trading_dates
            if fold.test_start <= value <= fold.test_end
        ]
        execution_bars = bundle.execution_bars.filter(
            pl.col("date").cast(pl.String).is_between(
                fold.test_start, fold.test_end, closed="both"
            )
        ).sort(["date", "instrument"])
        actual_dates = set(execution_bars["date"].cast(pl.String).unique().to_list())
        if actual_dates != set(test_dates):
            raise ResearchBundleError(
                f"{fold.fold_id} execution bars do not cover every test session"
            )

        for strategy_name in strategy_names:
            targets = _validate_fold_targets(
                strategy_targets[strategy_name][fold.fold_id],
                fold=fold,
                test_dates=test_dates,
            )
            result = run_backtest(execution_bars, targets, cfg)
            report = summarize_backtest(result)
            provenance = {
                **bundle.provenance,
                "fold": fold.to_dict(),
                "strategy_name": strategy_name,
                "execution_window": {"start": fold.test_start, "end": fold.test_end},
                "signal_cutoff": fold.test_end,
                "training_dataset_unlocked": False,
            }
            item = FoldStrategyResult(
                strategy_name=strategy_name,
                fold=fold,
                targets=targets,
                result=result,
                report=report,
                provenance=provenance,
            )
            all_results.append(item)
            by_strategy[strategy_name].append(item)

    summaries = {
        name: _aggregate_fold_reports(name, by_strategy[name])
        for name in strategy_names
    }
    manifest = {
        "acceptance_report_id": bundle.provenance.get("acceptance_report_id"),
        "source_revisions": bundle.provenance.get("source_revisions", {}),
        "universe_profile": bundle.provenance.get("universe_profile"),
        "folds": [fold.to_dict() for fold in checked_folds],
        "strategies": strategy_names,
        "backtest_config": {
            "initial_cash": cfg.initial_cash,
            "top_n": cfg.top_n,
            "board_lot": cfg.board_lot,
            "allow_leverage": cfg.allow_leverage,
            "costs": {
                "commission_rate": cfg.costs.commission_rate,
                "minimum_commission": cfg.costs.minimum_commission,
                "stamp_duty_sell_rate": cfg.costs.stamp_duty_sell_rate,
                "transfer_fee_rate": cfg.costs.transfer_fee_rate,
                "slippage_bps": cfg.costs.slippage_bps,
            },
        },
        "training_dataset_unlocked": False,
        "continuous_nav_constructed": False,
    }
    return WalkForwardExperimentResult(
        fold_results=tuple(all_results),
        summaries=summaries,
        manifest=manifest,
    )


def run_walk_forward_momentum(
    bundle: ResearchDataBundle,
    folds: list[WalkForwardFold],
    *,
    strategy_config: MomentumConfig | None = None,
    backtest_config: BacktestConfig | None = None,
) -> WalkForwardExperimentResult:
    """Generate fold-local momentum targets and execute OOS benchmark windows.

    For each fold the signal calculator receives only the bundle prefix ending
    at that fold's test_end. Only test-period signals before the final test
    session can enter the execution runner.
    """
    strategy = strategy_config or MomentumConfig()
    execution = backtest_config or BacktestConfig(top_n=strategy.top_n)
    if execution.top_n != strategy.top_n:
        raise ValueError("MomentumConfig.top_n and BacktestConfig.top_n must match")
    checked_folds = _validate_folds(bundle, folds)
    per_fold_targets: dict[str, pl.DataFrame] = {}
    for fold in checked_folds:
        prefix_sessions = [
            value for value in bundle.trading_dates if value <= fold.test_end
        ]
        prefix_prices = bundle.adjusted_bars.filter(
            pl.col("date").cast(pl.String) <= fold.test_end
        )
        prefix_membership = bundle.universe_membership.filter(
            pl.col("date").cast(pl.String) <= fold.test_end
        )
        scores = momentum_scores(
            prefix_prices,
            prefix_membership,
            prefix_sessions,
            strategy,
        ).filter(
            (pl.col("signal_date") >= fold.test_start)
            & (pl.col("signal_date") < fold.test_end)
        )
        if scores.is_empty():
            raise ResearchBundleError(
                f"{fold.fold_id} has no valid out-of-sample momentum scores"
            )
        per_fold_targets[fold.fold_id] = equal_weight_targets(
            scores.select("signal_date", "instrument", "score"),
            top_n=strategy.top_n,
        )
    return run_walk_forward_experiments(
        bundle,
        checked_folds,
        {"momentum": per_fold_targets},
        backtest_config=execution,
    )
