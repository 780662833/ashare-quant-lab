"""Chronological walk-forward runner for the Phase 9 cross-sectional learner.

The runner is an explicit callable workflow, not an automatic job. It requires
a validated research bundle and per-fold admission, fits only inside the
training window, and sends test predictions to the shared execution engine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import prod
from statistics import mean, median
from typing import Any, Mapping

import polars as pl

from ashare_quant.backtest import (
    BacktestConfig,
    BacktestResult,
    run_backtest,
    summarize_backtest,
)
from ashare_quant.research.admission import (
    TrainingDatasetAdmission,
    admit_training_dataset,
)
from ashare_quant.research.bundle import ResearchDataBundle
from ashare_quant.research.lightgbm_model import (
    LightGBMConfig,
    LightGBMRegressionModel,
    fit_lightgbm_regressor,
)
from ashare_quant.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
)
from ashare_quant.strategies.cross_sectional import weekly_cross_sectional_model_targets


@dataclass(frozen=True, slots=True)
class WalkForwardModelFoldRun:
    """One fold's admissions, predictions, targets and test-only ledgers."""

    fold: WalkForwardFold
    training_admission: TrainingDatasetAdmission
    final_admission: TrainingDatasetAdmission
    final_training_rows: int
    validation_predictions: pl.DataFrame
    test_predictions: pl.DataFrame
    targets: pl.DataFrame
    daily: pl.DataFrame
    orders: pl.DataFrame
    holdings: pl.DataFrame
    pending_targets: pl.DataFrame
    report: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WalkForwardCrossSectionalModelRun:
    """All OOS model folds and concatenated test-only outputs."""

    folds: list[WalkForwardModelFoldRun]
    fold_table: pl.DataFrame
    oos_daily: pl.DataFrame
    oos_orders: pl.DataFrame
    oos_holdings: pl.DataFrame
    oos_pending_targets: pl.DataFrame
    aggregate_report: dict[str, Any]


def _filter_dates(
    frame: pl.DataFrame,
    *,
    start: str | None = None,
    end: str | None = None,
    column: str = "signal_date",
) -> pl.DataFrame:
    if column not in frame.columns:
        raise ValueError(f"Frame is missing date column {column!r}")
    values = pl.col(column).cast(pl.String)
    predicate = pl.lit(True)
    if start is not None:
        predicate = predicate & (values >= start)
    if end is not None:
        predicate = predicate & (values <= end)
    return frame.filter(predicate)


def _fold_report(
    result: BacktestResult,
    *,
    fold: WalkForwardFold,
    baseline_nav: float,
    annualization: int,
) -> dict[str, Any]:
    daily = result.daily.sort("date")
    if daily.height != fold.test_sessions:
        raise ValueError(
            f"{fold.fold_id} expected {fold.test_sessions} test NAV rows, received {daily.height}"
        )
    if str(daily["date"][0]) != fold.test_start or str(daily["date"][-1]) != fold.test_end:
        raise ValueError(f"{fold.fold_id} NAV dates do not match its test window")
    if not daily.height or baseline_nav <= 0:
        raise ValueError(f"{fold.fold_id} has no valid baseline/test NAV")

    report = summarize_backtest(result, annualization=annualization)
    final_nav = float(daily["nav"][-1])
    total_return = final_nav / baseline_nav - 1.0
    report["initial_nav"] = float(baseline_nav)
    report["final_nav"] = final_nav
    report["total_return"] = total_return
    report["cagr"] = (final_nav / baseline_nav) ** (annualization / fold.test_sessions) - 1.0
    report["period"] = {
        "start": fold.test_start,
        "end": fold.test_end,
        "sessions": fold.test_sessions,
    }
    report["model_training_performed"] = True
    report["training_dataset_unlocked"] = False
    return report


def _aggregate(
    folds: list[WalkForwardModelFoldRun],
    *,
    trading_dates: list[str],
    annualization: int,
) -> dict[str, Any]:
    returns = [float(item.report["total_return"]) for item in folds]
    sharpes = [float(item.report["sharpe_ratio"]) for item in folds]
    drawdowns = [float(item.report["max_drawdown"]) for item in folds]
    sessions = sum(item.fold.test_sessions for item in folds)
    index = {value: position for position, value in enumerate(trading_dates)}
    contiguous = all(
        index[current.fold.test_start] == index[previous.fold.test_end] + 1
        for previous, current in zip(folds, folds[1:])
    )
    linked_return = prod(1.0 + value for value in returns) - 1.0
    return {
        "fold_count": len(folds),
        "out_of_sample_sessions": sessions,
        "test_windows_contiguous": contiguous,
        "mean_fold_total_return": mean(returns),
        "median_fold_total_return": median(returns),
        "positive_fold_ratio": sum(value > 0 for value in returns) / len(returns),
        "mean_fold_sharpe": mean(sharpes),
        "worst_fold_max_drawdown": min(drawdowns),
        "fold_linked_total_return": linked_return,
        "fold_linked_cagr": (
            (1.0 + linked_return) ** (annualization / sessions) - 1.0
            if contiguous and sessions else None
        ),
        "model_training_performed": True,
        "training_dataset_unlocked": False,
        "interpretation": (
            "Model was fitted independently inside each chronological fold. "
            "Validation labels mature before final test-window fitting; OOS "
            "predictions are generated only for that fold's test dates."
        ),
    }


def run_walk_forward_cross_sectional_model(
    bundle: ResearchDataBundle,
    *,
    acceptance_report: Mapping[str, Any],
    features: pl.DataFrame,
    labels: pl.DataFrame,
    walk_forward_config: WalkForwardConfig | None = None,
    model_config: LightGBMConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    top_n: int = 20,
    annualization: int = 252,
) -> WalkForwardCrossSectionalModelRun:
    """Fit and evaluate the cross-sectional model in chronological OOS folds.

    Per fold:
      1. Fit a diagnostic model on train rows admitted before validation.
      2. Predict validation rows without using their labels.
      3. Re-admit and refit on train+validation only after all labels mature.
      4. Predict the test window and run those targets through the shared engine.

    This function performs real model fitting only when explicitly called.
    CI exercises the orchestration with a fake LightGBM module and synthetic
    frames; this module is never invoked by package import or CI setup.
    """
    if not isinstance(bundle, ResearchDataBundle):
        raise TypeError("bundle must be a validated ResearchDataBundle")
    bundle._require_validated()
    if isinstance(annualization, bool) or not isinstance(annualization, int) or annualization < 1:
        raise ValueError("annualization must be a positive integer")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    if not isinstance(acceptance_report, Mapping):
        raise TypeError("acceptance_report must be a mapping")
    if not isinstance(features, pl.DataFrame) or not isinstance(labels, pl.DataFrame):
        raise TypeError("features and labels must be Polars DataFrames")
    if bundle.provenance.get("training_dataset_unlocked") is not False:
        raise ValueError("Model runner requires the acceptance report's training lock to remain false")
    if acceptance_report.get("report_id") != bundle.provenance.get("acceptance_report_id"):
        raise ValueError("Acceptance report identity does not match the validated research bundle")
    if acceptance_report.get("window") != bundle.provenance.get("acceptance_report_window"):
        raise ValueError("Acceptance report window does not match the validated research bundle")
    if acceptance_report.get("profile") != bundle.provenance.get("universe_profile"):
        raise ValueError("Acceptance report profile does not match the validated research bundle")
    if not {"signal_date", "instrument"}.issubset(features.columns):
        raise ValueError("features require signal_date and instrument")
    if not {"signal_date", "instrument", "label_end_date", "forward_return"}.issubset(labels.columns):
        raise ValueError("labels do not satisfy the forward-return label contract")

    sessions = list(bundle.trading_dates)
    session_set = set(sessions)
    weekly_dates = set()
    from ashare_quant.backtest.engine import weekly_rebalance_dates
    weekly_dates.update(weekly_rebalance_dates(sessions))
    for name, frame in (("features", features), ("labels", labels)):
        values = set(frame["signal_date"].cast(pl.String).to_list())
        if not values.issubset(session_set):
            raise ValueError(f"{name} contains signal dates outside the bundle calendar")
        if not values.issubset(weekly_dates):
            raise ValueError(f"{name} contains non-weekly signal dates")

    report_window = acceptance_report["window"]
    start_date = str(report_window["start"])
    end_date = str(report_window["end"])
    source_provenance = dict(bundle.provenance)
    cfg = model_config or LightGBMConfig()
    execution = backtest_config or BacktestConfig(top_n=top_n)
    if execution.top_n != top_n:
        raise ValueError("top_n and BacktestConfig.top_n must match")

    planned = build_walk_forward_folds(sessions, walk_forward_config)
    positions = {session: index for index, session in enumerate(sessions)}
    folds: list[WalkForwardModelFoldRun] = []

    for fold in planned:
        # First fit: train only, with all labels mature before validation starts.
        train_features = _filter_dates(features, end=fold.train_end)
        train_labels = _filter_dates(labels, end=fold.train_end)
        train_labels = train_labels.filter(
            pl.col("label_end_date").cast(pl.String) < fold.validation_start
        )
        train_admission = admit_training_dataset(
            acceptance_report=acceptance_report,
            source_provenance=source_provenance,
            features=train_features,
            labels=train_labels,
            start_date=start_date,
            end_date=end_date,
            training_cutoff=fold.validation_start,
            universe_profile=str(bundle.provenance["universe_profile"]),
            label_horizon_sessions=fold.label_horizon_sessions,
        )
        if not train_admission.admitted:
            raise PermissionError(
                f"{fold.fold_id} train admission failed: {list(train_admission.rejection_reasons)}"
            )
        diagnostic_model = fit_lightgbm_regressor(
            features=train_features,
            labels=train_labels,
            admission=train_admission,
            config=cfg,
        )
        validation_features = _filter_dates(
            features, start=fold.validation_start, end=fold.validation_end
        )
        validation_predictions = diagnostic_model.predict(validation_features)

        # Final fit: include validation-period examples only after their labels
        # have matured strictly before the test start.
        final_features = _filter_dates(features, end=fold.validation_end)
        final_labels = _filter_dates(labels, end=fold.validation_end)
        final_labels = final_labels.filter(
            pl.col("label_end_date").cast(pl.String) < fold.test_start
        )
        final_admission = admit_training_dataset(
            acceptance_report=acceptance_report,
            source_provenance=source_provenance,
            features=final_features,
            labels=final_labels,
            start_date=start_date,
            end_date=end_date,
            training_cutoff=fold.test_start,
            universe_profile=str(bundle.provenance["universe_profile"]),
            label_horizon_sessions=fold.label_horizon_sessions,
        )
        if not final_admission.admitted:
            raise PermissionError(
                f"{fold.fold_id} final admission failed: {list(final_admission.rejection_reasons)}"
            )
        final_model = fit_lightgbm_regressor(
            features=final_features,
            labels=final_labels,
            admission=final_admission,
            config=cfg,
        )
        test_features = _filter_dates(features, start=fold.test_start, end=fold.test_end)
        test_predictions = final_model.predict(test_features)
        targets = weekly_cross_sectional_model_targets(test_predictions, top_n=top_n)
        if targets.is_empty():
            raise ValueError(f"{fold.fold_id} produced no test-period model targets")

        test_start_index = positions[fold.test_start]
        if test_start_index < 1:
            raise ValueError(f"{fold.fold_id} has no preceding-session baseline")
        baseline_date = sessions[test_start_index - 1]
        execution_start = baseline_date
        fold_bars = bundle.execution_bars.filter(
            (pl.col("date").cast(pl.String) >= execution_start)
            & (pl.col("date").cast(pl.String) <= fold.test_end)
        )
        expected_dates = sessions[test_start_index - 1 : positions[fold.test_end] + 1]
        observed_dates = sorted(fold_bars["date"].cast(pl.String).unique().to_list())
        if observed_dates != expected_dates:
            raise ValueError(f"{fold.fold_id} execution bars do not cover baseline through test_end")
        full_result = run_backtest(fold_bars, targets, execution)
        test_daily = _filter_dates(full_result.daily, start=fold.test_start, end=fold.test_end, column="date")
        test_orders = _filter_dates(full_result.orders, start=fold.test_start, end=fold.test_end, column="date")
        test_holdings = _filter_dates(full_result.holdings, start=fold.test_start, end=fold.test_end, column="date")
        baseline = full_result.daily.filter(pl.col("date").cast(pl.String) == baseline_date)
        if baseline.height != 1:
            raise ValueError(f"{fold.fold_id} missing baseline NAV on {baseline_date}")
        baseline_nav = float(baseline["nav"].item())
        pending = full_result.pending_targets
        report = _fold_report(
            BacktestResult(
                daily=test_daily,
                orders=test_orders,
                holdings=test_holdings,
                pending_targets=pending,
            ),
            fold=fold,
            baseline_nav=baseline_nav,
            annualization=annualization,
        )
        fold_provenance = {
            **source_provenance,
            "walk_forward": {
                "fold": asdict(fold),
                "baseline_date": baseline_date,
                "baseline_nav": baseline_nav,
                "training_admission": train_admission.to_dict(),
                "final_admission": final_admission.to_dict(),
                "diagnostic_model": {
                    "training_rows": diagnostic_model.training_rows,
                    "training_cutoff": diagnostic_model.training_cutoff,
                    "source_fingerprint": diagnostic_model.source_fingerprint,
                },
                "final_model": {
                    "training_rows": final_model.training_rows,
                    "training_cutoff": final_model.training_cutoff,
                    "acceptance_report_id": final_model.acceptance_report_id,
                    "source_fingerprint": final_model.source_fingerprint,
                    "feature_fingerprint": final_model.feature_fingerprint,
                    "label_fingerprint": final_model.label_fingerprint,
                },
                "feature_data_cutoff_date": fold.test_end,
                "execution_data_cutoff_date": fold.test_end,
                "model_training_performed": True,
                "training_dataset_unlocked": False,
            },
        }
        folds.append(
            WalkForwardModelFoldRun(
                fold=fold,
                training_admission=train_admission,
                final_admission=final_admission,
                final_training_rows=final_model.training_rows,
                validation_predictions=validation_predictions,
                test_predictions=test_predictions,
                targets=targets,
                daily=test_daily,
                orders=test_orders,
                holdings=test_holdings,
                pending_targets=pending,
                report=report,
                provenance=fold_provenance,
            )
        )

    fold_table = pl.DataFrame([
        {
            "fold_id": item.fold.fold_id,
            "train_end": item.fold.train_end,
            "validation_start": item.fold.validation_start,
            "validation_end": item.fold.validation_end,
            "test_start": item.fold.test_start,
            "test_end": item.fold.test_end,
            "training_rows": item.final_training_rows,
            "test_prediction_rows": item.test_predictions.height,
            "total_return": item.report["total_return"],
            "cagr": item.report["cagr"],
            "sharpe_ratio": item.report["sharpe_ratio"],
            "max_drawdown": item.report["max_drawdown"],
            "order_count": item.report["execution"]["order_count"],
            "no_fill_order_count": item.report["execution"]["no_fill_order_count"],
            "total_transaction_costs": item.report["costs"]["total_transaction_costs"],
        }
        for item in folds
    ]).sort("fold_id")
    oos_daily = pl.concat([item.daily for item in folds], how="vertical_relaxed").sort("date")
    oos_orders = pl.concat([item.orders for item in folds], how="vertical_relaxed").sort(
        ["date", "instrument"]
    )
    oos_holdings = pl.concat([item.holdings for item in folds], how="vertical_relaxed").sort(
        ["date", "instrument"]
    )
    oos_pending = pl.concat(
        [item.pending_targets for item in folds], how="vertical_relaxed"
    )
    return WalkForwardCrossSectionalModelRun(
        folds=folds,
        fold_table=fold_table,
        oos_daily=oos_daily,
        oos_orders=oos_orders,
        oos_holdings=oos_holdings,
        oos_pending_targets=oos_pending,
        aggregate_report=_aggregate(folds, trading_dates=sessions, annualization=annualization),
    )
