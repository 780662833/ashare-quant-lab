"""Chronological walk-forward fold planning without fitting models.

This module generates reproducible temporal partitions only. It never creates
features/labels, trains models, reads CNEquity, or unlocks training.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Expanding (default) or rolling chronological fold parameters.

    The gap between train and validation, and between validation and test,
    equals label_horizon_sessions + embargo_sessions. This ensures that all
    labels anchored on the previous partition's final session have matured
    before the next partition begins, with the requested extra embargo.
    """

    min_train_sessions: int = 504
    validation_sessions: int = 63
    test_sessions: int = 63
    step_sessions: int | None = None
    label_horizon_sessions: int = 5
    embargo_sessions: int = 1
    expanding_train: bool = True

    def __post_init__(self) -> None:
        if self.min_train_sessions < 2:
            raise ValueError("min_train_sessions must be at least 2")
        if self.validation_sessions < 1 or self.test_sessions < 1:
            raise ValueError("validation_sessions and test_sessions must be positive")
        if self.label_horizon_sessions < 0 or self.embargo_sessions < 0:
            raise ValueError("label horizon and embargo must be non-negative")
        if self.step_sessions is not None:
            if self.step_sessions < self.test_sessions:
                raise ValueError("step_sessions must be >= test_sessions to avoid test overlap")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One chronological train/validation/test fold."""

    fold_id: str
    train_start: str
    train_end: str
    train_label_maturity: str
    validation_start: str
    validation_end: str
    validation_label_maturity: str
    test_start: str
    test_end: str
    train_sessions: int
    validation_sessions: int
    test_sessions: int
    label_horizon_sessions: int
    embargo_sessions: int
    expanding_train: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_calendar(trading_dates: list[Any]) -> list[str]:
    if not isinstance(trading_dates, list) or len(trading_dates) < 2:
        raise ValueError("trading_dates must be a list containing at least two sessions")
    normalized: list[str] = []
    for value in trading_dates:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"trading_dates contains an invalid ISO date: {text!r}") from exc
        if parsed.isoformat() != text:
            raise ValueError(f"trading_dates must use YYYY-MM-DD, got {text!r}")
        normalized.append(text)
    if len(normalized) != len(set(normalized)):
        raise ValueError("trading_dates must contain unique sessions")
    if normalized != sorted(normalized):
        raise ValueError("trading_dates must be strictly chronological; do not silently reorder")
    return normalized


def build_walk_forward_folds(
    trading_dates: list[Any],
    config: WalkForwardConfig | None = None,
) -> list[WalkForwardFold]:
    """Build expanding/rolling chronological folds from an official calendar.

    Fold layout (session indices are inclusive):
      train -> label horizon + embargo gap -> validation
      -> label horizon + embargo gap -> test

    Test windows are non-overlapping. By default the next fold advances by
    exactly test_sessions, so OOS test windows are contiguous where possible.
    Later folds may use earlier validation/test history as training data once
    its labels have matured; that is normal walk-forward behavior.

    This function only defines split boundaries. It does not access data,
    fit/tune models, generate labels, evaluate strategy returns, or override
    the four-gate model-training admission contract.
    """
    cfg = config or WalkForwardConfig()
    sessions = _normalize_calendar(trading_dates)
    gap = cfg.label_horizon_sessions + cfg.embargo_sessions
    step = cfg.step_sessions or cfg.test_sessions

    folds: list[WalkForwardFold] = []
    train_end_index = cfg.min_train_sessions - 1
    while True:
        validation_start_index = train_end_index + gap + 1
        validation_end_index = validation_start_index + cfg.validation_sessions - 1
        test_start_index = validation_end_index + gap + 1
        test_end_index = test_start_index + cfg.test_sessions - 1
        if test_end_index >= len(sessions):
            break

        train_start_index = (
            0 if cfg.expanding_train
            else train_end_index - cfg.min_train_sessions + 1
        )
        train_label_maturity_index = train_end_index + cfg.label_horizon_sessions
        validation_label_maturity_index = validation_end_index + cfg.label_horizon_sessions

        if train_label_maturity_index >= validation_start_index:
            raise AssertionError("Train labels do not mature before validation")
        if validation_label_maturity_index >= test_start_index:
            raise AssertionError("Validation labels do not mature before test")

        folds.append(
            WalkForwardFold(
                fold_id=f"fold_{len(folds) + 1:03d}",
                train_start=sessions[train_start_index],
                train_end=sessions[train_end_index],
                train_label_maturity=sessions[train_label_maturity_index],
                validation_start=sessions[validation_start_index],
                validation_end=sessions[validation_end_index],
                validation_label_maturity=sessions[validation_label_maturity_index],
                test_start=sessions[test_start_index],
                test_end=sessions[test_end_index],
                train_sessions=train_end_index - train_start_index + 1,
                validation_sessions=cfg.validation_sessions,
                test_sessions=cfg.test_sessions,
                label_horizon_sessions=cfg.label_horizon_sessions,
                embargo_sessions=cfg.embargo_sessions,
                expanding_train=cfg.expanding_train,
            )
        )
        train_end_index += step

    if not folds:
        minimum_required = (
            cfg.min_train_sessions + cfg.validation_sessions + cfg.test_sessions + 2 * gap
        )
        raise ValueError(
            "Insufficient trading sessions for one walk-forward fold: "
            f"received={len(sessions)}, minimum_required={minimum_required}"
        )
    return folds
