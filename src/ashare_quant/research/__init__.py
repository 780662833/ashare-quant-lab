"""Canonical research data and benchmark orchestration."""

from ashare_quant.research.benchmark import MomentumBenchmarkRun, run_momentum_benchmark
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.bundle import ResearchDataBundle, build_research_data_bundle
from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)

__all__ = [
    "MomentumBenchmarkRun",
    "ResearchBundleError",
    "ResearchDataBundle",
    "WalkForwardConfig",
    "WalkForwardFold",
    "build_research_data_bundle",
    "build_walk_forward_folds",
    "run_momentum_benchmark",
]
