"""Explicit fill-stress capability assessment; unsupported fills are never fabricated."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import polars as pl

from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment, verify_walk_forward_experiment
from ashare_quant.research.qa import _assess_verified_experiment

PROTOCOL_VERSION = "phase10.5"
TARGET_FILL_RATIOS = (1.0, 0.95, 0.90, 0.80, 0.70)
_UNSUPPORTED_REASON = (
    "The current backtest engine has no deterministic target-fill-ratio policy. "
    "It models tradability blocks and cash-affordability partial fills only; "
    "no target-ratio fills are fabricated or applied after accounting.",
)

def _observed_execution(orders: pl.DataFrame) -> dict[str, Any]:
    required = {"requested_qty", "filled_qty", "status", "reason"}
    missing = required.difference(orders.columns)
    if missing:
        raise ValueError(f"Verified OOS order ledger is missing fill fields: {sorted(missing)}")
    if orders["filled_qty"].null_count():
        raise ValueError("Verified OOS order ledger contains null filled_qty")
    allowed = {"filled", "partially_filled", "no_fill"}
    statuses = set(orders["status"].drop_nulls().unique().to_list())
    if not statuses.issubset(allowed) or orders["status"].null_count():
        raise ValueError("Verified OOS order ledger contains unknown fill status")
    requested_total = 0
    filled_total = 0
    reasons: Counter[str] = Counter()
    for row in orders.select("requested_qty", "filled_qty", "status", "reason").iter_rows(named=True):
        requested = row["requested_qty"]
        filled = row["filled_qty"]
        if isinstance(filled, bool) or not isinstance(filled, int) or filled < 0:
            raise ValueError("filled_qty must be a non-negative integer")
        if requested is not None:
            if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
                raise ValueError("requested_qty must be a non-negative integer or null")
            if filled > requested:
                raise ValueError("filled_qty cannot exceed requested_qty")
            requested_total += requested
        elif filled:
            raise ValueError("An order without requested_qty cannot contain a fill")
        filled_total += filled
        if row["status"] == "no_fill":
            reasons[str(row["reason"] or "unspecified")] += 1
    has_transition_identity = {"target_signal_date", "target_requested_qty", "instrument"}.issubset(orders.columns)
    if has_transition_identity:
        target_requests = (
            orders.group_by(["target_signal_date", "instrument"])
            .agg(pl.col("target_requested_qty").max().alias("target_requested_qty"))
        )
        requested_total = int(target_requests["target_requested_qty"].sum() or 0)
        fill_ratio_basis = "target_transition"
    else:
        fill_ratio_basis = "attempt_requested_legacy"
    ratio = filled_total / requested_total if requested_total else None
    if ratio is not None and not 0.0 <= ratio <= 1.0:
        raise ValueError("Observed baseline share fill ratio is outside [0, 1]")
    return {
        "order_count": orders.height,
        "requested_shares": requested_total,
        "filled_shares": filled_total,
        "share_fill_ratio": ratio,
        "share_fill_ratio_basis": fill_ratio_basis,
        "filled_order_count": orders.filter(pl.col("status") == "filled").height,
        "partially_filled_order_count": orders.filter(pl.col("status") == "partially_filled").height,
        "no_fill_order_count": orders.filter(pl.col("status") == "no_fill").height,
        "no_fill_reasons": dict(sorted(reasons.items())),
    }

def build_fill_stress_assessment(experiment_path: str | Path) -> dict[str, Any]:
    """Verify a baseline and report target-fill scenarios as unsupported or blocked.

    This function is deliberately read-only. It does not rerun the engine, change
    order ledgers, delete trades, or haircut NAV. Target ratios remain unsupported
    until a deterministic order-level policy is implemented and accounting-tested.
    """
    experiment = verify_walk_forward_experiment(str(experiment_path))
    if not isinstance(experiment, VerifiedWalkForwardExperiment):
        raise TypeError("experiment must come from verify_walk_forward_experiment()")
    admission = _assess_verified_experiment(experiment)
    observed = _observed_execution(experiment.oos_orders)
    scenario_status = "BLOCKED" if admission["decision"] == "BLOCKED" else "UNSUPPORTED"
    reason = "; ".join(admission["blockers"]) if scenario_status == "BLOCKED" else _UNSUPPORTED_REASON
    scenarios = [
        {
            "scenario_id": f"F{index}",
            "target_fill_ratio": ratio,
            "status": scenario_status,
            "achieved_fill_ratio": None,
            "reason": reason,
        }
        for index, ratio in enumerate(TARGET_FILL_RATIOS)
    ]
    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": str(experiment.manifest["experiment_id"]),
        "admission_decision": admission["decision"],
        "observed_baseline_execution": observed,
        "engine_capabilities": {
            "tradability_no_fill_reasons": True,
            "cash_affordability_partial_fills": True,
            "retry_unfilled_target_differences": True,
            "deterministic_target_fill_ratio_policy": False,
            "target_transition_ledger": "target_transition" == observed["share_fill_ratio_basis"],
        },
        "scenarios": scenarios,
        "model_training_performed": False,
        "training_dataset_unlocked": False,
        "limitations": [_UNSUPPORTED_REASON] if scenario_status == "UNSUPPORTED" else [reason],
    }
