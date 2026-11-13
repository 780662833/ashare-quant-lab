"""Atomic, provenance-bound persistence for completed walk-forward experiments.

This module stores already completed experiment outputs. It does not read
CNEquity, fetch data, create labels, or start model training.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import polars as pl

from ashare_quant.research.walk_forward_runner import WalkForwardMomentumRun

MANIFEST_SCHEMA_VERSION = 1
_REQUIRED_REVISIONS = {
    "daily_bars",
    "adj_factors",
    "universe",
    "trading_status",
    "trading_calendar",
    "price_limit_rules",
}
_ARTIFACT_FRAMES = {
    "fold_table": "fold_table.parquet",
    "oos_daily": "oos_daily.parquet",
    "oos_orders": "oos_orders.parquet",
    "oos_holdings": "oos_holdings.parquet",
}


def _json_value(value: Any) -> Any:
    """Convert supported values to strict JSON-compatible primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Experiment metadata must not contain NaN or infinity")
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Unsupported experiment metadata value: {type(value).__name__}")


def _config_payload(value: Any, *, name: str) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise TypeError(f"{name} must be a dataclass instance or mapping")
    normalized = _json_value(payload)
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(
            _json_value(payload),
            stream,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_experiment_id(experiment_id: str) -> str:
    if not isinstance(experiment_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", experiment_id
    ):
        raise ValueError(
            "experiment_id must be 1-80 characters using letters, digits, '.', '_' or '-'"
        )
    if experiment_id in {".", ".."}:
        raise ValueError("experiment_id must not be a relative path")
    return experiment_id


def _common_provenance(run: WalkForwardMomentumRun) -> dict[str, Any]:
    if not run.folds:
        raise ValueError("Cannot persist a walk-forward run without folds")
    if run.aggregate_report.get("model_training_performed") is not False:
        raise ValueError("Experiment store accepts benchmark runs with no model training")
    if run.aggregate_report.get("training_dataset_unlocked") is not False:
        raise ValueError("Experiment store requires training_dataset_unlocked=false")

    common: dict[str, Any] | None = None
    for fold_run in run.folds:
        provenance = dict(fold_run.provenance)
        walk_forward = provenance.pop("walk_forward", None)
        if not isinstance(walk_forward, Mapping):
            raise ValueError("Each fold must retain walk_forward provenance")
        if provenance.get("training_dataset_unlocked") is not False:
            raise ValueError("Fold provenance must keep training_dataset_unlocked=false")
        if walk_forward.get("training_dataset_unlocked") is not False:
            raise ValueError("Fold metadata must keep training_dataset_unlocked=false")
        if walk_forward.get("model_training_performed") is not False:
            raise ValueError("Fold metadata must confirm model_training_performed=false")
        if walk_forward.get("data_cutoff_date") != fold_run.fold.test_end:
            raise ValueError("Fold provenance cutoff does not match the fold test_end")
        normalized = _json_value(provenance)
        if common is None:
            common = normalized
        elif normalized != common:
            raise ValueError("Walk-forward folds do not share identical data provenance")

        if fold_run.daily.is_empty() or "date" not in fold_run.daily.columns:
            raise ValueError(f"{fold_run.fold.fold_id} has no dated daily NAV ledger")
        dates = fold_run.daily["date"].cast(pl.String)
        if str(dates[0]) != fold_run.fold.test_start or str(dates[-1]) != fold_run.fold.test_end:
            raise ValueError(f"{fold_run.fold.fold_id} daily ledger does not match its test window")
        if dates.n_unique() != fold_run.fold.test_sessions:
            raise ValueError(f"{fold_run.fold.fold_id} daily ledger has duplicate/missing sessions")
        for name, frame in (("orders", fold_run.orders), ("holdings", fold_run.holdings)):
            if frame.height and "date" not in frame.columns:
                raise ValueError(f"{fold_run.fold.fold_id} {name} ledger has no date column")
            if frame.height:
                frame_dates = frame["date"].cast(pl.String)
                if frame_dates.min() < fold_run.fold.test_start or frame_dates.max() > fold_run.fold.test_end:
                    raise ValueError(f"{fold_run.fold.fold_id} {name} ledger escapes its test window")

    assert common is not None
    fold_ids = [item.fold.fold_id for item in run.folds]
    if len(fold_ids) != len(set(fold_ids)):
        raise ValueError("Walk-forward fold IDs must be unique")
    expected_rows = {
        "fold_table": len(run.folds),
        "oos_daily": sum(item.daily.height for item in run.folds),
        "oos_orders": sum(item.orders.height for item in run.folds),
        "oos_holdings": sum(item.holdings.height for item in run.folds),
    }
    for name, expected in expected_rows.items():
        if getattr(run, name).height != expected:
            raise ValueError(f"run.{name} row count does not match its fold ledgers")

    combined_sort_columns = {
        "oos_daily": ["date"],
        "oos_orders": ["date", "instrument"],
        "oos_holdings": ["date", "instrument"],
    }
    for name, sort_columns in combined_sort_columns.items():
        fold_frames = [getattr(item, name.replace("oos_", "")) for item in run.folds]
        expected_frame = pl.concat(fold_frames, how="vertical_relaxed").sort(sort_columns)
        actual_frame = getattr(run, name).sort(sort_columns)
        if not actual_frame.equals(expected_frame):
            raise ValueError(f"run.{name} contents do not match its fold ledgers")

    if "date" not in run.oos_daily.columns:
        raise ValueError("Combined OOS daily ledger must contain date")
    if run.oos_daily["date"].cast(pl.String).n_unique() != run.oos_daily.height:
        raise ValueError("Combined OOS daily ledger contains duplicate sessions")

    required_provenance = {
        "acceptance_report_id",
        "acceptance_report_window",
        "universe_profile",
        "source_revisions",
        "source_receipts",
        "training_dataset_unlocked",
    }
    missing = required_provenance.difference(common)
    if missing:
        raise ValueError(f"Experiment provenance missing fields: {sorted(missing)}")
    revisions = common.get("source_revisions")
    if not isinstance(revisions, Mapping):
        raise ValueError("Experiment provenance source_revisions must be a mapping")
    missing_revisions = _REQUIRED_REVISIONS.difference(revisions)
    if missing_revisions:
        raise ValueError(
            f"Experiment provenance missing source revisions: {sorted(missing_revisions)}"
        )
    invalid_revisions = sorted(
        key for key in _REQUIRED_REVISIONS
        if not isinstance(revisions[key], str) or not revisions[key].strip()
    )
    if invalid_revisions:
        raise ValueError(
            f"Experiment provenance has empty/invalid source revisions: {invalid_revisions}"
        )
    for key in ("acceptance_report_id", "universe_profile"):
        if not isinstance(common.get(key), str) or not common[key].strip():
            raise ValueError(f"Experiment provenance {key} must be non-empty")
    receipts = common.get("source_receipts")
    if not isinstance(receipts, Mapping):
        raise ValueError("Experiment provenance source_receipts must be a mapping")
    for name in ("historical_universe", "hfq"):
        receipt = receipts.get(name)
        if not isinstance(receipt, Mapping) or receipt.get("replayable") is not True:
            raise ValueError(f"Experiment provenance is missing replayable {name} receipt")
    return common


def persist_walk_forward_momentum_run(
    run: WalkForwardMomentumRun,
    *,
    root: str | Path,
    experiment_id: str,
    repository_commit: str,
    walk_forward_config: Any,
    strategy_config: Any,
    backtest_config: Any,
    random_seed: int | None = None,
) -> Path:
    """Persist a completed momentum walk-forward run without overwriting prior runs.

    The destination is created atomically. A manifest binds the explicit
    configs, repository commit, common data provenance, per-fold cutoffs,
    aggregate report, and SHA-256 hashes of all Parquet/JSON result artifacts.
    """
    if not isinstance(run, WalkForwardMomentumRun):
        raise TypeError("run must be a WalkForwardMomentumRun")
    run_id = _validate_experiment_id(experiment_id)
    if not isinstance(repository_commit, str) or not re.fullmatch(
        r"[0-9a-fA-F]{7,40}", repository_commit
    ):
        raise ValueError("repository_commit must be a 7-40 character hexadecimal Git SHA")
    if random_seed is not None and (
        isinstance(random_seed, bool) or not isinstance(random_seed, int)
    ):
        raise TypeError("random_seed must be an integer or None")

    configs = {
        "walk_forward": _config_payload(walk_forward_config, name="walk_forward_config"),
        "strategy": _config_payload(strategy_config, name="strategy_config"),
        "backtest": _config_payload(backtest_config, name="backtest_config"),
    }
    provenance = _common_provenance(run)
    config_bytes = json.dumps(
        configs, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()

    output_root = Path(root).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = output_root.resolve()
    destination = output_root / run_id
    if destination.exists():
        raise FileExistsError(f"Experiment already exists and will not be overwritten: {destination}")

    staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.tmp-", dir=output_root))
    try:
        artifacts: dict[str, dict[str, Any]] = {}
        for name, filename in _ARTIFACT_FRAMES.items():
            frame = getattr(run, name)
            if not isinstance(frame, pl.DataFrame):
                raise TypeError(f"run.{name} must be a Polars DataFrame")
            artifact_path = staging / filename
            frame.write_parquet(artifact_path)
            artifacts[name] = {
                "file": filename,
                "rows": frame.height,
                "columns": frame.columns,
                "sha256": _sha256_file(artifact_path),
            }

        folds_payload = [fold_run.to_dict() for fold_run in run.folds]
        folds_path = staging / "folds.json"
        _write_json(folds_path, folds_payload)
        artifacts["folds"] = {
            "file": "folds.json",
            "rows": len(folds_payload),
            "sha256": _sha256_file(folds_path),
        }

        package_version = "unknown"
        try:
            from importlib.metadata import version

            package_version = version("ashare-quant-lab")
        except Exception:
            pass

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "experiment_id": run_id,
            "experiment_type": "walk_forward_momentum",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repository_commit": repository_commit.lower(),
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "ashare_quant_lab": package_version,
                "polars": pl.__version__,
            },
            "random_seed": random_seed,
            "configs": configs,
            "config_sha256": config_sha256,
            "data_provenance": provenance,
            "aggregate_report": run.aggregate_report,
            "artifacts": artifacts,
            "training_dataset_unlocked": False,
            "model_training_performed": False,
        }
        _write_json(staging / "manifest.json", manifest)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination
