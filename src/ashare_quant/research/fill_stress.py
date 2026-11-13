"""Explicit fill-stress capability assessment; unsupported fills are never fabricated."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest import BacktestConfig, CostModel
from ashare_quant.research.experiment_compare import VerifiedWalkForwardExperiment, verify_walk_forward_experiment
from ashare_quant.research.experiment_store import persist_walk_forward_momentum_run
from ashare_quant.research.qa import _assess_verified_experiment, build_research_qa_report
from ashare_quant.research.walk_forward import WalkForwardConfig
from ashare_quant.research.walk_forward_runner import run_walk_forward_momentum
from ashare_quant.strategies.momentum import MomentumConfig

PROTOCOL_VERSION = "phase10.5"
TARGET_FILL_RATIOS = (1.0, 0.95, 0.90, 0.80, 0.70)
_UNSUPPORTED_REASON = (
    "No verified immutable F0-F4 fill-stress suite was supplied. "
    "The read-only assessment does not run scenarios or fabricate achieved fills.",
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
    has_fill_cap = {"target_fill_cap_qty", "target_fill_ratio"}.issubset(orders.columns)
    if has_transition_identity:
        aggregations = [
            pl.col("target_requested_qty").min().alias("_min_target_requested"),
            pl.col("target_requested_qty").max().alias("target_requested_qty"),
            pl.col("filled_qty").sum().alias("_transition_filled"),
        ]
        if has_fill_cap:
            aggregations.extend([
                pl.col("target_fill_cap_qty").min().alias("_min_fill_cap"),
                pl.col("target_fill_cap_qty").max().alias("target_fill_cap_qty"),
                pl.col("target_fill_ratio").min().alias("_min_fill_ratio"),
                pl.col("target_fill_ratio").max().alias("target_fill_ratio"),
            ])
        target_requests = orders.group_by(["target_signal_date", "instrument"]).agg(aggregations)
        if target_requests.filter(
            pl.col("_min_target_requested").is_not_null()
            & (pl.col("_min_target_requested") != pl.col("target_requested_qty"))
        ).height:
            raise ValueError("Target requested quantity changes across retry attempts")
        if target_requests.filter(
            pl.col("_transition_filled") > pl.col("target_requested_qty").fill_null(0)
        ).height:
            raise ValueError("Cumulative fills exceed target transition quantity")
        requested_total = int(target_requests["target_requested_qty"].sum() or 0)
        fill_ratio_basis = "target_transition"
        if has_fill_cap:
            if target_requests.filter(
                pl.col("_min_fill_cap").is_not_null()
                & (pl.col("_min_fill_cap") != pl.col("target_fill_cap_qty"))
            ).height:
                raise ValueError("Target fill cap changes across retry attempts")
            if target_requests.filter(
                pl.col("_min_fill_ratio") != pl.col("target_fill_ratio")
            ).height:
                raise ValueError("Target fill ratio changes across retry attempts")
            if target_requests.filter(
                pl.col("_transition_filled") > pl.col("target_fill_cap_qty").fill_null(0)
            ).height:
                raise ValueError("Cumulative fills exceed target fill cap")
            cap_total = int(target_requests["target_fill_cap_qty"].sum() or 0)
            if cap_total > requested_total:
                raise ValueError("Target fill caps exceed target requested shares")
        else:
            cap_total = None
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
        "target_fill_cap_shares": cap_total if has_transition_identity and has_fill_cap else None,
        "target_fill_cap_ratio": (
            cap_total / requested_total
            if has_transition_identity and has_fill_cap and requested_total > 0 else None
        ),
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


def _backtest_config_from_manifest(payload: Mapping[str, Any]) -> BacktestConfig:
    values = dict(payload)
    costs = values.pop("costs", {})
    return BacktestConfig(**values, costs=CostModel(**costs))


def _fold_signature(folds: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    signature = []
    for item in folds:
        fold = item.get("fold")
        if not isinstance(fold, Mapping):
            raise ValueError("Verified experiment fold is missing its definition")
        signature.append(tuple(str(fold.get(key)) for key in (
            "fold_id", "train_start", "train_end", "validation_start",
            "validation_end", "test_start", "test_end", "test_sessions",
        )))
    return signature


def _assert_fill_scenario_alignment(experiments: list[VerifiedWalkForwardExperiment]) -> None:
    reference = experiments[0]
    ref_manifest = reference.manifest
    ref_configs = ref_manifest["configs"]
    ref_provenance = ref_manifest["data_provenance"]
    ref_folds = _fold_signature(reference.folds)
    for item in experiments[1:]:
        manifest = item.manifest
        configs = manifest["configs"]
        if manifest["data_provenance"] != ref_provenance:
            raise ValueError("Fill scenarios do not share identical data provenance/revisions")
        if configs["walk_forward"] != ref_configs["walk_forward"]:
            raise ValueError("Fill scenarios use different walk-forward configurations")
        if configs["strategy"] != ref_configs["strategy"]:
            raise ValueError("Fill scenarios use different strategy configurations")
        reference_backtest = dict(ref_configs["backtest"])
        candidate_backtest = dict(configs["backtest"])
        reference_backtest.pop("target_fill_ratio", None)
        candidate_backtest.pop("target_fill_ratio", None)
        if reference_backtest != candidate_backtest:
            raise ValueError("Fill scenarios changed non-fill backtest settings")
        if _fold_signature(item.folds) != ref_folds:
            raise ValueError("Fill scenarios do not share identical OOS fold windows")


def _fill_scenario_metrics(
    experiment: VerifiedWalkForwardExperiment,
    *,
    scenario_id: str,
    target_fill_ratio: float,
) -> dict[str, Any]:
    aggregate = experiment.manifest.get("aggregate_report")
    if not isinstance(aggregate, Mapping):
        raise ValueError("Verified fill experiment is missing aggregate_report")
    observed = _observed_execution(experiment.oos_orders)
    if observed["share_fill_ratio_basis"] != "target_transition":
        raise ValueError("Fill-stress scenario requires target-transition order identity")
    cap_ratio = observed["target_fill_cap_ratio"]
    achieved_ratio = observed["share_fill_ratio"]
    if cap_ratio is not None and cap_ratio > target_fill_ratio + 1e-9:
        raise ValueError("Scenario target-fill cap exceeds its registered ratio")
    if achieved_ratio is not None and achieved_ratio > target_fill_ratio + 1e-9:
        raise ValueError("Achieved fill ratio exceeds its registered target ratio")
    return {
        "scenario_id": scenario_id,
        "experiment_id": experiment.manifest["experiment_id"],
        "target_fill_ratio": target_fill_ratio,
        "target_fill_cap_ratio": cap_ratio,
        "achieved_fill_ratio": achieved_ratio,
        "target_requested_shares": observed["requested_shares"],
        "target_fill_cap_shares": observed["target_fill_cap_shares"],
        "filled_shares": observed["filled_shares"],
        "order_count": observed["order_count"],
        "filled_order_count": observed["filled_order_count"],
        "partially_filled_order_count": observed["partially_filled_order_count"],
        "no_fill_order_count": observed["no_fill_order_count"],
        "no_fill_reasons": observed["no_fill_reasons"],
        "net_return": aggregate.get("fold_linked_total_return")
            if aggregate.get("test_windows_contiguous") is True
            else aggregate.get("mean_fold_total_return"),
        "cagr": aggregate.get("fold_linked_cagr")
            if aggregate.get("test_windows_contiguous") is True
            else aggregate.get("mean_fold_cagr"),
        "worst_fold_max_drawdown": aggregate.get("worst_fold_max_drawdown"),
        "aggregation": "fold_linked" if aggregate.get("test_windows_contiguous") is True
            else "mean_fold; test gaps excluded",
    }


def run_fill_stress_suite(
    bundle: Any,
    *,
    baseline_experiment_path: str | Path,
    experiment_root: str | Path,
    experiment_id_prefix: str,
    repository_commit: str,
) -> dict[str, Any]:
    """Run immutable F0-F4 target-fill variants through the shared walk-forward engine."""
    baseline = verify_walk_forward_experiment(baseline_experiment_path)
    if baseline.manifest.get("experiment_type") != "walk_forward_momentum":
        raise ValueError("F0 baseline must be a walk_forward_momentum experiment")
    if baseline.manifest.get("training_dataset_unlocked") is not False:
        raise ValueError("F0 baseline must keep model training locked")
    if baseline.manifest.get("model_training_performed") is not False:
        raise ValueError("F0 baseline must confirm no model training")

    configs = baseline.manifest["configs"]
    walk_forward_config = WalkForwardConfig(**configs["walk_forward"])
    strategy_config = MomentumConfig(**configs["strategy"])
    baseline_backtest = _backtest_config_from_manifest(configs["backtest"])
    if baseline_backtest.target_fill_ratio != 1.0:
        raise ValueError("F0 baseline must use target_fill_ratio=1.0")
    baseline_qa = build_research_qa_report(
        str(baseline.path), qa_id=f"p105-{baseline.manifest['experiment_id']}-F0",
        created_at=datetime.now(timezone.utc).isoformat(),
        repository_commit=repository_commit, bundle=bundle,
    )
    if baseline_qa["admission"]["decision"] != "PRICE_ONLY_ELIGIBLE":
        raise ValueError("F0 baseline is not PRICE_ONLY_ELIGIBLE for the supplied canonical bundle")

    verified = [baseline]
    paths = [baseline.path]
    admissions = [baseline_qa["admission"]["decision"]]
    scenario_specs = list(zip(
        ("F0", "F1", "F2", "F3", "F4"), TARGET_FILL_RATIOS,
    ))
    for scenario_id, ratio in scenario_specs[1:]:
        scenario_config = replace(baseline_backtest, target_fill_ratio=ratio)
        run = run_walk_forward_momentum(
            bundle,
            walk_forward_config=walk_forward_config,
            strategy_config=strategy_config,
            backtest_config=scenario_config,
        )
        experiment_id = f"{experiment_id_prefix}-{scenario_id}"
        path = persist_walk_forward_momentum_run(
            run, root=experiment_root, experiment_id=experiment_id,
            repository_commit=repository_commit,
            walk_forward_config=walk_forward_config,
            strategy_config=strategy_config,
            backtest_config=scenario_config,
        )
        item = verify_walk_forward_experiment(path)
        qa = build_research_qa_report(
            str(path), qa_id=f"p105-{experiment_id}",
            created_at=datetime.now(timezone.utc).isoformat(),
            repository_commit=repository_commit, bundle=bundle,
        )
        if qa["admission"]["decision"] != "PRICE_ONLY_ELIGIBLE":
            raise ValueError(f"{scenario_id} is not PRICE_ONLY_ELIGIBLE for the supplied bundle")
        verified.append(item)
        paths.append(path)
        admissions.append(qa["admission"]["decision"])

    _assert_fill_scenario_alignment(verified)
    metrics = [
        _fill_scenario_metrics(item, scenario_id=scenario_id, target_fill_ratio=ratio)
        for item, (scenario_id, ratio) in zip(verified, scenario_specs)
    ]
    report = {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository_commit": repository_commit.lower(),
        "baseline_experiment_id": baseline.manifest["experiment_id"],
        "experiment_ids": [item.manifest["experiment_id"] for item in verified],
        "scenario_ratios": list(TARGET_FILL_RATIOS),
        "scenarios": metrics,
        "integrity": {
            "all_artifacts_verified": True,
            "admission_decisions": admissions,
            "config_sha256": [item.manifest["config_sha256"] for item in verified],
            "data_provenance_sha256": hashlib.sha256(
                json.dumps(
                    baseline.manifest["data_provenance"],
                    sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        },
        "limitations": [
            "Achieved fill ratios may be below target caps because of suspensions, price-limit locks, missing bars, cash affordability and board-lot rounding.",
            "Target fill ratios cap cumulative order fills per signal transition; they are not forecasts of real market liquidity or broker execution.",
            "Results are conditional price-only benchmark evidence and do not validate LightGBM or unlock model training.",
            "CAGR uses fold-linked OOS windows only when test folds are contiguous; otherwise it is mean fold CAGR and excludes gaps.",
        ],
        "training_dataset_unlocked": False,
        "model_training_performed": False,
    }
    report["integrity"]["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return report
