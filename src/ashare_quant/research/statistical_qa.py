"""Descriptive, dependence-aware-by-caveat statistics over verified OOS artifacts."""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

import polars as pl

from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment, verify_walk_forward_experiment
from ashare_quant.research.qa import _assess_verified_experiment

PROTOCOL_VERSION = "phase10.6"
_METRICS = (
    "total_return", "cagr", "annualized_volatility", "sharpe_ratio",
    "max_drawdown", "total_transaction_costs",
)

def _distribution(values: list[float], *, missing_count: int) -> dict[str, Any]:
    if not values:
        return {
            "sample_count": 0, "missing_count": missing_count, "mean": None,
            "median": None, "sample_std": None, "q1": None, "q3": None,
            "iqr": None, "min": None, "max": None,
        }
    ordered = sorted(values)
    q1 = float(pl.Series(ordered).quantile(0.25, interpolation="linear"))
    q3 = float(pl.Series(ordered).quantile(0.75, interpolation="linear"))
    return {
        "sample_count": len(values),
        "missing_count": missing_count,
        "mean": mean(values),
        "median": median(values),
        "sample_std": stdev(values) if len(values) > 1 else None,
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "min": ordered[0],
        "max": ordered[-1],
    }

def _finite_values(frame: pl.DataFrame, column: str) -> tuple[list[float], int]:
    values: list[float] = []
    missing = 0
    for value in frame[column].to_list():
        if value is None:
            missing += 1
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
            raise ValueError(f"fold_table.{column} contains a non-finite or non-numeric value")
        values.append(float(value))
    return values, missing

def _validate_daily_ledger(daily: pl.DataFrame, expected_sessions: int) -> dict[str, Any]:
    required = {"date", "nav", "daily_return"}
    missing = required.difference(daily.columns)
    if missing or daily.is_empty():
        raise ValueError(f"Verified OOS daily ledger is missing fields or empty: {sorted(missing)}")
    dates = [str(value) for value in daily["date"].to_list()]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise ValueError("Verified OOS daily ledger dates must be unique and chronological")
    if daily["nav"].null_count() or daily["daily_return"].null_count():
        raise ValueError("Verified OOS daily ledger contains null NAV/return values")
    nav = [float(value) for value in daily["nav"].to_list()]
    returns = [float(value) for value in daily["daily_return"].to_list()]
    if any(not isfinite(value) or value <= 0 for value in nav):
        raise ValueError("Verified OOS daily ledger contains invalid NAV")
    if any(not isfinite(value) or value <= -1.0 for value in returns):
        raise ValueError("Verified OOS daily ledger contains invalid daily returns")
    if daily.height != expected_sessions:
        raise ValueError(
            f"Verified OOS daily session count {daily.height} does not match fold total {expected_sessions}"
        )
    return {
        "observed_sessions": daily.height,
        "expected_fold_sessions": expected_sessions,
        "coverage_ratio": daily.height / expected_sessions if expected_sessions else 0.0,
        "start": dates[0],
        "end": dates[-1],
        "duplicate_dates": 0,
    }

def _execution_summary(orders: pl.DataFrame) -> dict[str, Any]:
    required = {"requested_qty", "filled_qty", "status", "reason"}
    missing = required.difference(orders.columns)
    if missing:
        raise ValueError(f"Verified OOS order ledger is missing fields: {sorted(missing)}")
    requested = 0
    filled = 0
    reasons: dict[str, int] = {}
    for row in orders.select("requested_qty", "filled_qty", "status", "reason").iter_rows(named=True):
        req, fill = row["requested_qty"], row["filled_qty"]
        if isinstance(fill, bool) or not isinstance(fill, int) or fill < 0:
            raise ValueError("filled_qty must be a non-negative integer")
        if req is not None:
            if isinstance(req, bool) or not isinstance(req, int) or req < 0 or fill > req:
                raise ValueError("requested_qty must be non-negative and at least filled_qty")
            requested += req
        elif fill:
            raise ValueError("An order without requested_qty cannot contain a fill")
        if row["status"] == "no_fill":
            reason = str(row["reason"] or "unspecified")
            reasons[reason] = reasons.get(reason, 0) + 1
    if orders["status"].null_count():
        raise ValueError("Verified OOS order ledger contains null status")
    status_values = set(orders["status"].unique().to_list())
    if not status_values.issubset({"filled", "partially_filled", "no_fill"}):
        raise ValueError("Verified OOS order ledger contains unsupported status values")
    filled_total = sum(int(value) for value in orders["filled_qty"].to_list())
    return {
        "order_count": orders.height,
        "filled_order_count": orders.filter(pl.col("status") == "filled").height,
        "partially_filled_order_count": orders.filter(pl.col("status") == "partially_filled").height,
        "no_fill_order_count": orders.filter(pl.col("status") == "no_fill").height,
        "requested_shares": requested,
        "filled_shares": filled_total,
        "share_fill_ratio": filled_total / requested if requested else None,
        "no_fill_reasons": dict(sorted(reasons.items())),
    }

def build_statistical_qa_summary(experiment_path: str | Path) -> dict[str, Any]:
    """Build descriptive fold statistics from a verified experiment; perform no inference.

    Daily returns are not treated as IID observations. No p-values, confidence
    intervals, or benchmark excess ratios are invented when the required design
    inputs are absent.
    """
    experiment = verify_walk_forward_experiment(str(experiment_path))
    if not isinstance(experiment, VerifiedWalkForwardExperiment):
        raise TypeError("experiment must come from verify_walk_forward_experiment()")
    admission = _assess_verified_experiment(experiment)
    folds = experiment.fold_table
    if folds.is_empty() or "fold_id" not in folds.columns or "test_sessions" not in folds.columns:
        raise ValueError("Verified fold table is empty or missing fold identity/session fields")
    if "total_return" not in folds.columns:
        raise ValueError("Verified fold table is missing total_return")
    if folds["fold_id"].null_count() or folds["fold_id"].n_unique() != folds.height:
        raise ValueError("Verified fold table contains missing/duplicate fold IDs")
    session_values = folds["test_sessions"].to_list()
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in session_values):
        raise ValueError("Fold test_sessions must be positive integers")
    expected_sessions = sum(session_values)
    daily_summary = _validate_daily_ledger(experiment.oos_daily, expected_sessions)
    execution = _execution_summary(experiment.oos_orders)

    if admission["decision"] == "BLOCKED":
        summaries = None
        worst_fold = None
        decision = "BLOCKED"
        limitations = list(admission["blockers"])
    else:
        summaries = {}
        for metric in _METRICS:
            if metric not in folds.columns:
                summaries[metric] = _distribution([], missing_count=folds.height)
                continue
            values, missing_count = _finite_values(folds, metric)
            summaries[metric] = _distribution(values, missing_count=missing_count)
        total_returns, _missing_returns = _finite_values(folds, "total_return")
        return_rows = [
            (str(row["fold_id"]), float(row["total_return"]))
            for row in folds.select("fold_id", "total_return").iter_rows(named=True)
            if row["total_return"] is not None
        ]
        worst_fold = (
            {"fold_id": min(return_rows, key=lambda item: item[1])[0],
             "total_return": min(return_rows, key=lambda item: item[1])[1]}
            if return_rows else None
        )
        decision = "DESCRIPTIVE_ONLY"
        limitations = [
            "No dependence-aware statistical inference is implemented; fold summaries are descriptive only.",
            "No independent benchmark ledger is attached; positive excess-fold ratio is unavailable.",
            "Parameter multiplicity/selection history is not present in the verified experiment manifest.",
        ]
    report = {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": str(experiment.manifest["experiment_id"]),
        "admission_decision": admission["decision"],
        "decision": decision,
        "sample_counts": {
            "fold_count": folds.height,
            "oos_session_count": daily_summary["observed_sessions"],
            "order_count": execution["order_count"],
        },
        "oos_daily_coverage": daily_summary,
        "fold_metric_distributions": summaries,
        "worst_fold": worst_fold,
        "positive_fold_ratio": (
            (sum(value > 0 for value in total_returns) / len(total_returns) if total_returns else None) if summaries is not None else None
        ),
        "positive_excess_fold_ratio": {
            "value": None, "status": "NOT_AVAILABLE",
            "reason": "No independent benchmark fold ledger is attached to this experiment.",
        },
        "execution": execution,
        "statistical_inference": {
            "status": "NOT_IMPLEMENTED", "method": None, "p_values": None,
            "reason": "Daily and fold returns are dependent; no IID inference is assumed.",
        },
        "parameter_multiplicity": {
            "status": "NOT_ASSESSED", "variant_count": None,
            "selection_after_test_observation": None,
        },
        "limitations": limitations,
        "model_training_performed": False,
        "training_dataset_unlocked": False,
    }
    json.dumps(report, ensure_ascii=False, allow_nan=False)
    return report
