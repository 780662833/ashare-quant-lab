"""Canonical research data and benchmark orchestration."""

from ashare_quant.research.benchmark import MomentumBenchmarkRun, run_momentum_benchmark
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.bundle import ResearchDataBundle, build_research_data_bundle
from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)
from ashare_quant.research.walk_forward_runner import (
    FoldStrategyResult,
    WalkForwardExperimentResult,
    run_walk_forward_experiments,
    run_walk_forward_momentum,
)

__all__ = [
    "FoldStrategyResult",
    "MomentumBenchmarkRun",
    "ResearchBundleError",
    "ResearchDataBundle",
    "WalkForwardConfig",
    "WalkForwardExperimentResult",
    "WalkForwardFold",
    "build_research_data_bundle",
    "build_walk_forward_folds",
    "run_momentum_benchmark",
    "run_walk_forward_experiments",
    "run_walk_forward_momentum",
]
