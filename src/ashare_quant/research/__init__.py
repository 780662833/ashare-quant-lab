"""Canonical research data and benchmark orchestration."""

from ashare_quant.research.benchmark import MomentumBenchmarkRun, run_momentum_benchmark
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.bundle import ResearchDataBundle, build_research_data_bundle
from ashare_quant.research.walk_forward_runner import (
    WalkForwardMomentumFoldRun,
    WalkForwardMomentumRun,
    run_walk_forward_momentum,
)
from ashare_quant.research.experiment_store import persist_walk_forward_momentum_run
from ashare_quant.research.experiment_compare import (
    VerifiedWalkForwardExperiment,
    WalkForwardExperimentComparison,
    compare_walk_forward_experiments,
    verify_walk_forward_experiment,
)

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
    "WalkForwardMomentumFoldRun",
    "WalkForwardMomentumRun",
    "VerifiedWalkForwardExperiment",
    "WalkForwardExperimentComparison",
    "build_research_data_bundle",
    "build_walk_forward_folds",
    "compare_walk_forward_experiments",
    "persist_walk_forward_momentum_run",
    "run_momentum_benchmark",
    "run_walk_forward_momentum",
    "verify_walk_forward_experiment",
]
