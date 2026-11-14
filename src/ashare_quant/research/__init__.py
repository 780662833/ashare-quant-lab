"""Canonical research data and benchmark orchestration."""

from ashare_quant.research.benchmark import MomentumBenchmarkRun, run_momentum_benchmark\nfrom ashare_quant.research.features import CrossSectionalFeatureConfig, build_cross_sectional_features\nfrom ashare_quant.research.labels import build_forward_return_labels
from ashare_quant.research.cnequity_assembler import assemble_cnequity_research_bundle
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

from ashare_quant.research.qa import build_research_qa_report, validate_research_qa_report
from ashare_quant.research.period_regime import build_period_regime_diagnostics
from ashare_quant.research.fill_stress import build_fill_stress_assessment
from ashare_quant.research.statistical_qa import build_statistical_qa_summary
from ashare_quant.research.integrated_qa import (
    build_integrated_research_qa_report,
    write_research_qa_report,
)
from ashare_quant.research.cost_stress import (
    CostStressScenario,
    build_cost_stress_scenarios,
    run_cost_stress_suite,
)

from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)

__all__ = [
    "MomentumBenchmarkRun",\n    "CrossSectionalFeatureConfig",\n    "build_cross_sectional_features",\n    "build_forward_return_labels",
    "ResearchBundleError",
    "ResearchDataBundle",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardMomentumFoldRun",
    "WalkForwardMomentumRun",
    "VerifiedWalkForwardExperiment",
    "WalkForwardExperimentComparison",
    "build_research_data_bundle",
    "assemble_cnequity_research_bundle",
    "build_research_qa_report",
    "build_period_regime_diagnostics",
    "CostStressScenario",
    "build_cost_stress_scenarios",
    "build_fill_stress_assessment",
    "build_statistical_qa_summary",
    "build_integrated_research_qa_report",
    "write_research_qa_report",
    "run_cost_stress_suite",
    "validate_research_qa_report",
    "build_walk_forward_folds",
    "compare_walk_forward_experiments",
    "persist_walk_forward_momentum_run",
    "run_momentum_benchmark",
    "run_walk_forward_momentum",
    "verify_walk_forward_experiment",
]
