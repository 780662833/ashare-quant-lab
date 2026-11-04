"""Deterministic research benchmark strategies."""

from ashare_quant.strategies.momentum import (
    MomentumConfig,
    momentum_scores,
    weekly_momentum_targets,
)

__all__ = ["MomentumConfig", "momentum_scores", "weekly_momentum_targets"]
