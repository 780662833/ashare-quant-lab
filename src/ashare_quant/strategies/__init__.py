"""Deterministic research benchmark strategies."""

from ashare_quant.strategies.cross_sectional import weekly_cross_sectional_model_targets
from ashare_quant.strategies.momentum import (
    MomentumConfig,
    momentum_scores,
    weekly_momentum_targets,
)

__all__ = [
    "MomentumConfig", "momentum_scores", "weekly_momentum_targets",
    "weekly_cross_sectional_model_targets",
]
