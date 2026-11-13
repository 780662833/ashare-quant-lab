"""Integrity verification and fair comparison of persisted walk-forward runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from statistics import median
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import polars as pl

from ashare_quant.research.experiment_store import MANIFEST_SCHEMA_VERSION
from ashare_quant.research.experiment_provenance import validate_experiment_provenance

_REQUIRED_ARTIFACTS = {
    "fold_table",
    "oos_daily",
    "oos_orders",
    "oos_holdings",
    "folds",
}
_FRAME_ARTIFACTS = {
    "fold_table": "fold_table.parquet",
    "oos_daily": "oos_daily.parquet",
    "oos_orders": "oos_orders.parquet",
    "oos_holdings": "oos_holdings.parquet",
}
_REQUIRED_REVISIONS = {
    "daily_bars",
    "adj_factors",
    "universe",
    "trading_status",
    "trading_calendar",
    "price_limit_rules",
}


@dataclass(frozen=True, slots=True)
class VerifiedWalkForwardExperiment:
    """Artifacts loaded only after manifest, provenance and file hashes verify."""

    path: Path
    manifest: dict[str, Any]
    folds: list[dict[str, Any]]
    fold_table: pl.DataFrame
    oos_daily: pl.DataFrame
    oos_orders: pl.DataFrame
    oos_holdings: pl.DataFrame


@dataclass(frozen=True, slots=True)
class WalkForwardExperimentComparison:
    """Aggregate and per-fold metrics for comparable momentum experiments."""

    summary: pl.DataFrame
    by_fold: pl.DataFrame


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _fold_signature(
    folds: list[dict[str, Any]],
) -> list[tuple[str, str, str, str, str, str, str, str]]:
    signature = []
    for item in folds:
        fold = item.get("fold")
        if not isinstance(fold, dict):
            raise ValueError("Fold manifest is missing its fold definition")
        signature.append((
            str(fold.get("fold_id")),
            str(fold.get("train_start")),
            str(fold.get("train_end")),
            str(fold.get("validation_start")),
            str(fold.get("validation_end")),
            str(fold.get("test_start")),
            str(fold.get("test_end")),
            str(fold.get("test_sessions")),
        ))
    return signature


def verify_walk_forward_experiment(
    experiment_path: str | Path,
) -> VerifiedWalkForwardExperiment:
    """Verify an experiment directory before exposing its manifest or ledgers.

    All artifact paths are restricted to the experiment directory. Every file
    hash, Parquet row count/schema, fold cutoff and training-lock marker is
    checked against the manifest.
    """
    directory = Path(experiment_path).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Experiment directory does not exist: {directory}")
    manifest_path = directory / "manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("Experiment manifest must be a JSON object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported experiment manifest schema_version")
    if manifest.get("experiment_type") != "walk_forward_momentum":
        raise ValueError("Unsupported experiment_type")
    if manifest.get("experiment_id") != directory.name:
        raise ValueError("Experiment directory name does not match manifest experiment_id")
    if manifest.get("training_dataset_unlocked") is not False:
        raise ValueError("Persisted experiment must keep training_dataset_unlocked=false")
    if manifest.get("model_training_performed") is not False:
        raise ValueError("Persisted momentum experiment must confirm no model training")
    if not isinstance(manifest.get("repository_commit"), str) or not re.fullmatch(
        r"[0-9a-f]{7,40}", manifest["repository_commit"]
    ):
        raise ValueError("Experiment manifest has an invalid repository_commit")

    configs = manifest.get("configs")
    if not isinstance(configs, dict) or set(configs) != {"walk_forward", "strategy", "backtest"}:
        raise ValueError("Experiment manifest is missing its explicit config sections")
    config_digest = hashlib.sha256(
        json.dumps(
            configs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if manifest.get("config_sha256") != config_digest:
        raise ValueError("Experiment config_sha256 does not match the stored configs")

    provenance = manifest.get("data_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Experiment manifest is missing data_provenance")
    if provenance.get("training_dataset_unlocked") is not False:
        raise ValueError("Experiment data provenance has training unlocked")
    if not isinstance(provenance.get("acceptance_report_id"), str) or not provenance["acceptance_report_id"]:
        raise ValueError("Experiment data provenance is missing acceptance_report_id")
    if not isinstance(provenance.get("universe_profile"), str) or not provenance["universe_profile"]:
        raise ValueError("Experiment data provenance is missing universe_profile")
    validate_experiment_provenance(provenance)

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != _REQUIRED_ARTIFACTS:
        raise ValueError("Experiment manifest artifact inventory is incomplete or unexpected")

    loaded_frames: dict[str, pl.DataFrame] = {}
    folds: list[dict[str, Any]] | None = None
    for name, record in artifacts.items():
        if not isinstance(record, dict):
            raise ValueError(f"Artifact record {name!r} must be an object")
        filename = record.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename or filename in {".", ".."}:
            raise ValueError(f"Artifact {name!r} has an unsafe filename")
        artifact_path = directory / filename
        if not artifact_path.is_file():
            raise ValueError(f"Experiment artifact is missing: {filename}")
        expected_hash = record.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"Artifact {name!r} has an invalid SHA-256 value")
        actual_hash = _sha256_file(artifact_path)
        if actual_hash != expected_hash:
            raise ValueError(f"SHA-256 mismatch for experiment artifact {filename}")

        if name == "folds":
            value = _read_json(artifact_path)
            if not isinstance(value, list):
                raise ValueError("folds.json must contain a JSON array")
            if record.get("rows") != len(value):
                raise ValueError("folds.json row count does not match its manifest")
            folds = value
            continue

        if filename != _FRAME_ARTIFACTS[name]:
            raise ValueError(f"Artifact {name!r} has an unexpected filename")
        frame = pl.read_parquet(artifact_path)
        if record.get("rows") != frame.height:
            raise ValueError(f"Parquet row count mismatch for {filename}")
        if record.get("columns") != frame.columns:
            raise ValueError(f"Parquet columns mismatch for {filename}")
        loaded_frames[name] = frame

    if folds is None or not folds:
        raise ValueError("Experiment must contain at least one fold manifest")
    if len(folds) != loaded_frames["fold_table"].height:
        raise ValueError("fold_table row count does not match folds.json")

    fold_ids = []
    test_ranges: list[tuple[str, str, int]] = []
    for item in folds:
        if not isinstance(item, dict) or not isinstance(item.get("fold"), dict):
            raise ValueError("folds.json contains a malformed fold record")
        fold = item["fold"]
        fold_id = fold.get("fold_id")
        test_start = fold.get("test_start")
        test_end = fold.get("test_end")
        test_sessions = fold.get("test_sessions")
        if not all(isinstance(value, str) and value for value in (fold_id, test_start, test_end)):
            raise ValueError("Fold manifest has incomplete identity or date bounds")
        if not isinstance(test_sessions, int) or test_sessions < 1:
            raise ValueError("Fold manifest test_sessions must be a positive integer")
        fold_ids.append(fold_id)
        test_ranges.append((test_start, test_end, test_sessions))

        fold_provenance = item.get("provenance")
        if not isinstance(fold_provenance, dict):
            raise ValueError(f"{fold_id} is missing fold provenance")
        common_fold_provenance = dict(fold_provenance)
        common_fold_provenance.pop("walk_forward", None)
        if common_fold_provenance != provenance:
            raise ValueError(f"{fold_id} data provenance does not match experiment manifest")
        wf_provenance = fold_provenance.get("walk_forward")
        if not isinstance(wf_provenance, dict):
            raise ValueError(f"{fold_id} is missing walk_forward provenance")
        if wf_provenance.get("data_cutoff_date") != test_end:
            raise ValueError(f"{fold_id} data cutoff does not match test_end")
        if wf_provenance.get("training_dataset_unlocked") is not False:
            raise ValueError(f"{fold_id} training lock is not preserved")
        if wf_provenance.get("model_training_performed") is not False:
            raise ValueError(f"{fold_id} claims model training was performed")

    if len(fold_ids) != len(set(fold_ids)):
        raise ValueError("Fold IDs in folds.json must be unique")
    if test_ranges != sorted(test_ranges):
        raise ValueError("Fold manifests are not in chronological test order")
    for previous, current in zip(test_ranges, test_ranges[1:]):
        if current[0] <= previous[1]:
            raise ValueError("Walk-forward test windows overlap")

    fold_table = loaded_frames["fold_table"]
    required_fold_columns = {"fold_id", "test_start", "test_end", "test_sessions"}
    if not required_fold_columns.issubset(fold_table.columns):
        raise ValueError("fold_table is missing fold identity/date columns")
    expected_fold_rows = [
        (
            item["fold"]["fold_id"],
            item["fold"]["test_start"],
            item["fold"]["test_end"],
            item["fold"]["test_sessions"],
        )
        for item in folds
    ]
    actual_fold_rows = [
        (
            str(row["fold_id"]),
            str(row["test_start"]),
            str(row["test_end"]),
            int(row["test_sessions"]),
        )
        for row in fold_table.to_dicts()
    ]
    if actual_fold_rows != expected_fold_rows:
        raise ValueError("fold_table definitions do not match folds.json")

    aggregate = manifest.get("aggregate_report")
    if not isinstance(aggregate, dict):
        raise ValueError("Experiment manifest is missing aggregate_report")
    fold_reports = [item.get("report") for item in folds]
    if any(not isinstance(report, dict) for report in fold_reports):
        raise ValueError("Fold manifest is missing a report")
    fold_returns = [float(report["total_return"]) for report in fold_reports]
    fold_cagrs = [float(report["cagr"]) for report in fold_reports]
    fold_sharpes = [float(report["sharpe_ratio"]) for report in fold_reports]
    fold_drawdowns = [float(report["max_drawdown"]) for report in fold_reports]
    expected_aggregate = {
        "fold_count": len(folds),
        "out_of_sample_sessions": sum(item["fold"]["test_sessions"] for item in folds),
        "mean_fold_total_return": sum(fold_returns) / len(fold_returns),
        "median_fold_total_return": median(fold_returns),
        "positive_fold_ratio": sum(value > 0 for value in fold_returns) / len(fold_returns),
        "mean_fold_cagr": sum(fold_cagrs) / len(fold_cagrs),
        "mean_fold_sharpe": sum(fold_sharpes) / len(fold_sharpes),
        "worst_fold_max_drawdown": min(fold_drawdowns),
        "fold_linked_total_return": math.prod(1.0 + value for value in fold_returns) - 1.0,
    }
    for key, expected in expected_aggregate.items():
        actual = aggregate.get(key)
        if isinstance(expected, int):
            matches = actual == expected
        else:
            matches = (
                isinstance(actual, (int, float))
                and math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-12)
            )
        if not matches:
            raise ValueError(f"Aggregate report metric {key} does not match fold reports")

    daily = loaded_frames["oos_daily"]
    if "date" not in daily.columns:
        raise ValueError("oos_daily is missing date")
    daily_dates = daily["date"].cast(pl.String)
    if daily_dates.n_unique() != daily.height:
        raise ValueError("oos_daily contains duplicate dates")
    expected_daily_rows = sum(item[2] for item in test_ranges)
    if daily.height != expected_daily_rows:
        raise ValueError("oos_daily row count does not match fold test_sessions")
    for start, end, sessions in test_ranges:
        subset = daily.filter(
            (pl.col("date").cast(pl.String) >= start)
            & (pl.col("date").cast(pl.String) <= end)
        )
        if subset.height != sessions or subset["date"].cast(pl.String).min() != start or subset["date"].cast(pl.String).max() != end:
            raise ValueError(f"oos_daily does not match fold test window {start}..{end}")

    for name in ("oos_orders", "oos_holdings"):
        frame = loaded_frames[name]
        if frame.height and "date" not in frame.columns:
            raise ValueError(f"{name} is missing date")
        if frame.height:
            date_values = frame["date"].cast(pl.String)
            if any(
                not any(start <= value <= end for start, end, _ in test_ranges)
                for value in date_values.to_list()
            ):
                raise ValueError(f"{name} contains rows outside all fold test windows")

    return VerifiedWalkForwardExperiment(
        path=directory,
        manifest=manifest,
        folds=folds,
        fold_table=fold_table,
        oos_daily=daily,
        oos_orders=loaded_frames["oos_orders"],
        oos_holdings=loaded_frames["oos_holdings"],
    )


def compare_walk_forward_experiments(
    experiment_paths: Sequence[str | Path],
) -> WalkForwardExperimentComparison:
    """Compare momentum parameter variants only on identical data and execution.

    Universe/profile, acceptance receipts, all source revisions, walk-forward
    splits and backtest/cost config must match exactly. Only strategy config
    may differ. The function reports metrics; it does not rank/select a model
    or tune parameters against the test windows.
    """
    if isinstance(experiment_paths, (str, Path)) or len(experiment_paths) < 2:
        raise ValueError("Provide at least two experiment directory paths")
    experiments = [verify_walk_forward_experiment(path) for path in experiment_paths]
    reference = experiments[0]
    reference_manifest = reference.manifest
    reference_configs = reference_manifest["configs"]
    reference_provenance = reference_manifest["data_provenance"]
    reference_signature = _fold_signature(reference.folds)

    for experiment in experiments[1:]:
        manifest = experiment.manifest
        configs = manifest["configs"]
        if manifest["data_provenance"] != reference_provenance:
            raise ValueError("Experiments do not share identical data provenance/revisions")
        if configs["walk_forward"] != reference_configs["walk_forward"]:
            raise ValueError("Experiments use different walk-forward configurations")
        if configs["backtest"] != reference_configs["backtest"]:
            raise ValueError("Experiments use different backtest/cost configurations")
        if _fold_signature(experiment.folds) != reference_signature:
            raise ValueError("Experiments do not share identical fold test windows")

    summary_rows = []
    fold_rows = []
    for experiment in experiments:
        manifest = experiment.manifest
        aggregate = manifest.get("aggregate_report")
        if not isinstance(aggregate, dict):
            raise ValueError("Experiment manifest is missing aggregate_report")
        strategy_config = manifest["configs"]["strategy"]
        summary_rows.append({
            "experiment_id": manifest["experiment_id"],
            "repository_commit": manifest["repository_commit"],
            "strategy_config": json.dumps(
                strategy_config, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ),
            "fold_count": aggregate["fold_count"],
            "out_of_sample_sessions": aggregate["out_of_sample_sessions"],
            "mean_fold_total_return": aggregate["mean_fold_total_return"],
            "median_fold_total_return": aggregate["median_fold_total_return"],
            "positive_fold_ratio": aggregate["positive_fold_ratio"],
            "mean_fold_cagr": aggregate["mean_fold_cagr"],
            "mean_fold_sharpe": aggregate["mean_fold_sharpe"],
            "worst_fold_max_drawdown": aggregate["worst_fold_max_drawdown"],
            "fold_linked_total_return": aggregate["fold_linked_total_return"],
        })
        for item in experiment.folds:
            fold = item["fold"]
            report = item["report"]
            fold_rows.append({
                "experiment_id": manifest["experiment_id"],
                "fold_id": fold["fold_id"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "test_sessions": fold["test_sessions"],
                "total_return": report["total_return"],
                "cagr": report["cagr"],
                "annualized_volatility": report["annualized_volatility"],
                "sharpe_ratio": report["sharpe_ratio"],
                "max_drawdown": report["max_drawdown"],
                "total_transaction_costs": report["costs"]["total_transaction_costs"],
            })

    return WalkForwardExperimentComparison(
        summary=pl.DataFrame(summary_rows),
        by_fold=pl.DataFrame(fold_rows),
    )
