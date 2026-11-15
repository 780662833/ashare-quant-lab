"""End-to-end momentum benchmark orchestration on a validated research bundle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from ashare_quant.backtest import BacktestConfig, BacktestResult, run_backtest, summarize_backtest
from ashare_quant.backtest.engine import equal_weight_targets
from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.strategies.momentum import MomentumConfig, momentum_scores


@dataclass(frozen=True, slots=True)
class MomentumBenchmarkRun:
    """Signals, targets, backtest accounting, report, and provenance."""

    scores: pl.DataFrame
    targets: pl.DataFrame
    result: BacktestResult
    report: dict[str, Any]
    provenance: dict[str, Any]


def run_momentum_benchmark(
    bundle: ResearchDataBundle,
    *,
    strategy_config: MomentumConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    signal_dates: list[str] | None = None,
) -> MomentumBenchmarkRun:
    """Run weekly momentum from canonical data through the shared engine.

    This is a price-only benchmark, not a model-training path. The bundle
    constructor has already required historical-universe, HFQ, and
    trading-status acceptance for the same scope and revisions.
    """
    bundle._require_validated()
    strategy = strategy_config or MomentumConfig()
    execution = backtest_config or BacktestConfig(top_n=strategy.top_n)
    if execution.top_n != strategy.top_n:
        raise ValueError("MomentumConfig.top_n and BacktestConfig.top_n must match")

    scores = momentum_scores(
        bundle.adjusted_bars,
        bundle.universe_membership,
        bundle.trading_dates,
        strategy,
        signal_dates=signal_dates,
    )
    targets = equal_weight_targets(
        scores.select("signal_date", "instrument", "score"),
        top_n=strategy.top_n,
    )
    if targets.is_empty():
        raise ValueError(
            "No momentum targets were generated; provide enough prior sessions "
            "for the configured lookback/skip window and weekly PIT membership"
        )

    result = run_backtest(bundle.execution_bars, targets, execution)
    report = summarize_backtest(result)
    return MomentumBenchmarkRun(
        scores=scores,
        targets=targets,
        result=result,
        report=report,
        provenance={
            **dict(bundle.provenance),
            "momentum_signal_dates": (
                scores["signal_date"].unique().sort().to_list()
                if not scores.is_empty() else []
            ),
        },
    )
