"""Pre-registered transaction-cost stress scenarios for verified momentum runs.

Cost variants reuse the Phase 8 runner and immutable experiment store. They
never modify existing artifacts or infer broker/exchange schedules.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest import BacktestConfig, CostModel
from ashare_quant.research.experiment_store import persist_walk_forward_momentum_run
from ashare_quant.research.experiment_compare import verify_walk_forward_experiment
from ashare_quant.research.qa import build_research_qa_report
from ashare_quant.research.walk_forward_runner import run_walk_forward_momentum
from ashare_quant.research.walk_forward import WalkForwardConfig
from ashare_quant.strategies.momentum import MomentumConfig

COST_STRESS_PROTOCOL = "phase10.4"
COST_STRESS_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0)
_SCENARIO_NAMES = ("C0_baseline", "C1_moderate", "C2_severe", "C3_extreme")


@dataclass(frozen=True, slots=True)
class CostStressScenario:
    """One fixed, versioned multiplier applied to every configured cost term."""

    scenario_id: str
    label: str
    multiplier: float
    backtest_config: BacktestConfig


def build_cost_stress_scenarios(
    baseline: BacktestConfig,
) -> tuple[CostStressScenario, ...]:
    """Build C0-C3 from the exact persisted baseline configuration."""
    if not isinstance(baseline, BacktestConfig):
        raise TypeError("baseline must be a BacktestConfig")
    scenarios = []
    for index, (label, multiplier) in enumerate(zip(_SCENARIO_NAMES, COST_STRESS_MULTIPLIERS)):
        costs = replace(
            baseline.costs,
            commission_rate=baseline.costs.commission_rate * multiplier,
            minimum_commission=baseline.costs.minimum_commission * multiplier,
            stamp_duty_sell_rate=baseline.costs.stamp_duty_sell_rate * multiplier,
            transfer_fee_rate=baseline.costs.transfer_fee_rate * multiplier,
            slippage_bps=baseline.costs.slippage_bps * multiplier,
        )
        scenarios.append(CostStressScenario(
            scenario_id=f"C{index}",
            label=label,
            multiplier=multiplier,
            backtest_config=replace(baseline, costs=costs),
        ))
    return tuple(scenarios)


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


def _without_costs(config: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "costs"}


def _scenario_metrics(experiment: Any, *, multiplier: float, scenario_id: str) -> dict[str, Any]:
    manifest = experiment.manifest
    aggregate = manifest.get("aggregate_report")
    if not isinstance(aggregate, Mapping):
        raise ValueError("Verified experiment is missing aggregate_report")
    contiguous = aggregate.get("test_windows_contiguous") is True
    net_return = (
        aggregate.get("fold_linked_total_return")
        if contiguous else aggregate.get("mean_fold_total_return")
    )
    cagr = (
        aggregate.get("fold_linked_cagr")
        if contiguous else aggregate.get("mean_fold_cagr")
    )
    if net_return is None or cagr is None:
        raise ValueError("Cost scenario has no comparable OOS return/CAGR summary")
    daily = experiment.oos_daily
    orders = experiment.oos_orders
    if daily.is_empty() or "nav" not in daily.columns:
        raise ValueError("Cost scenario has no OOS NAV ledger")
    average_nav = float(daily["nav"].mean())
    if average_nav <= 0:
        raise ValueError("Cost scenario average NAV must be positive")
    traded_notional = 0.0
    if orders.height and {"filled_qty", "fill_price"}.issubset(orders.columns):
        traded_notional = float(
            orders.select(
                (pl.col("filled_qty") * pl.col("fill_price").fill_null(0.0)).sum()
            ).item()
        )
    costs = aggregate.get("costs", {})
    execution = aggregate.get("execution", {})
    return {
        "scenario_id": scenario_id,
        "experiment_id": manifest["experiment_id"],
        "cost_multiplier": multiplier,
        "aggregation": "fold_linked" if contiguous else "mean_fold; test gaps excluded",
        "out_of_sample_sessions": int(aggregate.get("out_of_sample_sessions", daily.height)),
        "net_return": float(net_return),
        "cagr": float(cagr),
        "worst_fold_max_drawdown": float(aggregate.get("worst_fold_max_drawdown")),
        "turnover": traded_notional / average_nav,
        "order_count": int(execution.get("order_count", orders.height)),
        "filled_order_count": int(execution.get("filled_order_count", 0)),
        "no_fill_order_count": int(execution.get("no_fill_order_count", 0)),
        "costs": {
            key: float(costs.get(key, 0.0))
            for key in (
                "commission", "transfer_fee", "stamp_duty",
                "slippage_cost", "total_fees", "total_transaction_costs",
            )
        },
        "training_dataset_unlocked": manifest.get("training_dataset_unlocked"),
        "model_training_performed": manifest.get("model_training_performed"),
    }


def _assert_scenario_alignment(experiments: list[Any]) -> None:
    reference = experiments[0]
    ref_manifest = reference.manifest
    ref_configs = ref_manifest["configs"]
    ref_provenance = ref_manifest["data_provenance"]
    ref_folds = _fold_signature(reference.folds)
    for item in experiments[1:]:
        manifest = item.manifest
        configs = manifest["configs"]
        if manifest["data_provenance"] != ref_provenance:
            raise ValueError("Cost scenarios do not share identical data provenance/revisions")
        if configs["walk_forward"] != ref_configs["walk_forward"]:
            raise ValueError("Cost scenarios use different walk-forward configurations")
        if configs["strategy"] != ref_configs["strategy"]:
            raise ValueError("Cost scenarios use different strategy configurations")
        if _without_costs(configs["backtest"]) != _without_costs(ref_configs["backtest"]):
            raise ValueError("Cost scenarios changed non-cost backtest settings")
        if _fold_signature(item.folds) != ref_folds:
            raise ValueError("Cost scenarios do not share identical OOS fold windows")


def run_cost_stress_suite(
    bundle: Any,
    *,
    baseline_experiment_path: str | Path,
    experiment_root: str | Path,
    experiment_id_prefix: str,
    repository_commit: str,
) -> dict[str, Any]:
    """Run C1-C3 from a verified C0 artifact and return a verified comparison.

    The C0 baseline is never rerun or overwritten. Every stressed variant is
    run through the existing walk-forward runner and persisted immutably.
    """
    baseline = verify_walk_forward_experiment(baseline_experiment_path)
    if baseline.manifest.get("experiment_type") != "walk_forward_momentum":
        raise ValueError("C0 baseline must be a walk_forward_momentum experiment")
    if baseline.manifest.get("training_dataset_unlocked") is not False:
        raise ValueError("C0 baseline must keep model training locked")
    if baseline.manifest.get("model_training_performed") is not False:
        raise ValueError("C0 baseline must confirm no model training")

    configs = baseline.manifest["configs"]
    walk_forward_config = WalkForwardConfig(**configs["walk_forward"])
    strategy_config = MomentumConfig(**configs["strategy"])
    baseline_backtest = BacktestConfig(
        **{
            **{key: value for key, value in configs["backtest"].items() if key != "costs"},
            "costs": CostModel(**configs["backtest"]["costs"]),
        }
    )
    created_at = datetime.now(timezone.utc).isoformat()
    baseline_qa = build_research_qa_report(
        str(baseline.path),
        qa_id=f"p104-{baseline.manifest['experiment_id']}-C0",
        created_at=created_at,
        repository_commit=repository_commit,
        bundle=bundle,
    )
    if baseline_qa["admission"]["decision"] != "PRICE_ONLY_ELIGIBLE":
        raise ValueError("C0 baseline is not PRICE_ONLY_ELIGIBLE for the supplied canonical bundle")

    scenarios = build_cost_stress_scenarios(baseline_backtest)
    verified = [baseline]
    paths = [baseline.path]
    scenario_qa = [baseline_qa]
    for scenario in scenarios[1:]:
        run = run_walk_forward_momentum(
            bundle,
            walk_forward_config=walk_forward_config,
            strategy_config=strategy_config,
            backtest_config=scenario.backtest_config,
        )
        experiment_id = f"{experiment_id_prefix}-{scenario.scenario_id}"
        path = persist_walk_forward_momentum_run(
            run,
            root=experiment_root,
            experiment_id=experiment_id,
            repository_commit=repository_commit,
            walk_forward_config=walk_forward_config,
            strategy_config=strategy_config,
            backtest_config=scenario.backtest_config,
        )
        item = verify_walk_forward_experiment(path)
        qa = build_research_qa_report(
            str(path),
            qa_id=f"p104-{experiment_id}",
            created_at=datetime.now(timezone.utc).isoformat(),
            repository_commit=repository_commit,
            bundle=bundle,
        )
        if qa["admission"]["decision"] != "PRICE_ONLY_ELIGIBLE":
            raise ValueError(f"{scenario.scenario_id} is not PRICE_ONLY_ELIGIBLE for the supplied bundle")
        verified.append(item)
        paths.append(path)
        scenario_qa.append(qa)

    _assert_scenario_alignment(verified)
    metric_rows = [
        _scenario_metrics(item, multiplier=multiplier, scenario_id=f"C{index}")
        for index, (item, multiplier) in enumerate(zip(verified, COST_STRESS_MULTIPLIERS))
    ]
    baseline_metrics = metric_rows[0]
    for row in metric_rows:
        row["excess_return_vs_C0"] = row["net_return"] - baseline_metrics["net_return"]
        row["cagr_delta_vs_C0"] = row["cagr"] - baseline_metrics["cagr"]
        row["drawdown_delta_vs_C0"] = (
            row["worst_fold_max_drawdown"] - baseline_metrics["worst_fold_max_drawdown"]
        )
        row["transaction_cost_delta_vs_C0"] = (
            row["costs"]["total_transaction_costs"]
            - baseline_metrics["costs"]["total_transaction_costs"]
        )

    report = {
        "schema_version": 1,
        "protocol_version": COST_STRESS_PROTOCOL,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository_commit": repository_commit.lower(),
        "baseline_experiment_id": baseline.manifest["experiment_id"],
        "experiment_ids": [item.manifest["experiment_id"] for item in verified],
        "scenario_multipliers": list(COST_STRESS_MULTIPLIERS),
        "scenarios": metric_rows,
        "integrity": {
            "all_artifacts_verified": True,
            "config_sha256": [item.manifest["config_sha256"] for item in verified],
            "admission_decisions": [item["admission"]["decision"] for item in scenario_qa],
            "data_provenance_sha256": hashlib.sha256(
                json.dumps(
                    baseline.manifest["data_provenance"],
                    sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        },
        "limitations": [
            "Cost multipliers are explicit research assumptions, not current broker or exchange fee schedules.",
            "Excess return is the net-return difference versus C0 cost assumptions, not excess return versus a market benchmark.",
            "CAGR uses fold-linked OOS windows only when test folds are contiguous; otherwise it is the mean fold CAGR and excludes gaps.",
            "Results are conditional price-only benchmark evidence; they do not validate LightGBM or unlock model training.",
        ],
        "training_dataset_unlocked": False,
        "model_training_performed": False,
    }
    report["integrity"]["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return report
