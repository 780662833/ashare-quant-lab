"""Canonical research data and benchmark orchestration."""

from ashare_quant.research.benchmark import MomentumBenchmarkRun, run_momentum_benchmark
from ashare_quant.research.features import CrossSectionalFeatureConfig, build_cross_sectional_features
from ashare_quant.research.labels import build_forward_return_labels
from ashare_quant.research.ranking_diagnostics import evaluate_validation_ranking
from ashare_quant.research.missingness_diagnostics import evaluate_validation_feature_missingness
from ashare_quant.research.validation_stability import (
    ValidationRankingAcceptancePolicy,
    summarize_validation_stability,
)
from ashare_quant.research.admission import TrainingDatasetAdmission, admit_training_dataset
from ashare_quant.research.lightgbm_model import LightGBMConfig, LightGBMRegressionModel, fit_lightgbm_regressor
from ashare_quant.research.model_experiment_store import (
    persist_walk_forward_model_run,
    verify_walk_forward_model_run,
)
from ashare_quant.research.walk_forward_model_runner import (
    WalkForwardModelFoldRun,
    WalkForwardCrossSectionalModelRun,
    run_walk_forward_cross_sectional_model,
)
from ashare_quant.research.qlib_dataset import (
    QlibDatasetBundle,
    build_qlib_panel_rows,
    create_qlib_dataset,
)
from ashare_quant.research.qlib_lightgbm import (
    fit_qlib_lightgbm_regressor,
    predict_qlib_lightgbm_regressor,
)
from ashare_quant.research.cnequity_assembler import (
    assemble_cnequity_research_bundle,
    assemble_cnequity_pit_fundamentals,
    bind_pit_fundamental_provenance,
)
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
    "MomentumBenchmarkRun",
    "CrossSectionalFeatureConfig",
    "build_cross_sectional_features",
    "build_forward_return_labels",
    "evaluate_validation_ranking",
    "evaluate_validation_feature_missingness",
    "ValidationRankingAcceptancePolicy",
    "summarize_validation_stability",
    "TrainingDatasetAdmission",
    "admit_training_dataset",
    "LightGBMConfig",
    "LightGBMRegressionModel",
    "fit_lightgbm_regressor",
    "build_qlib_panel_rows",
    "create_qlib_dataset",
    "QlibDatasetBundle",
    "fit_qlib_lightgbm_regressor",
    "predict_qlib_lightgbm_regressor",
    "WalkForwardModelFoldRun",
    "WalkForwardCrossSectionalModelRun",
    "run_walk_forward_cross_sectional_model",
    "persist_walk_forward_model_run",
    "verify_walk_forward_model_run",
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
    "assemble_cnequity_pit_fundamentals",
    "bind_pit_fundamental_provenance",
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
