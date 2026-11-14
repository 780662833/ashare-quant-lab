"""Durable persistence and checksum verification for Phase 9 model runs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ashare_quant.research.walk_forward_model_runner import WalkForwardCrossSectionalModelRun

_STORE_SCHEMA_VERSION = 1
_EXPERIMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40,64}")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported manifest value type: {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            _jsonable(value),
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _config_payload(value: Any, name: str) -> Any:
    if value is None:
        raise ValueError(f"{name} must be supplied for reproducible model-run persistence")
    return _jsonable(value)


def persist_walk_forward_model_run(
    run: WalkForwardCrossSectionalModelRun,
    *,
    root: str | Path,
    experiment_id: str,
    repository_commit: str,
    walk_forward_config: Any,
    model_config: Any,
    backtest_config: Any,
    benchmark_config: Any,
    top_n: int = 20,
) -> Path:
    """Atomically persist fold models, predictions, ledgers and provenance."""
    if not isinstance(run, WalkForwardCrossSectionalModelRun):
        raise TypeError("run must be a WalkForwardCrossSectionalModelRun")
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError("experiment_id must be 1-64 safe ASCII characters")
    if not isinstance(repository_commit, str) or not _COMMIT_SHA.fullmatch(repository_commit):
        raise ValueError("repository_commit must be a 40-64 character lowercase git SHA")
    if not run.folds:
        raise ValueError("Cannot persist a model run without folds")
    if run.aggregate_report.get("model_training_performed") is not True:
        raise ValueError("Model run manifest must confirm model training was performed")
    if run.aggregate_report.get("training_dataset_unlocked") is not False:
        raise ValueError("Model run must preserve training_dataset_unlocked=false")
    for name, value in (
        ("walk_forward_config", walk_forward_config),
        ("model_config", model_config),
        ("backtest_config", backtest_config),
        ("benchmark_config", benchmark_config),
    ):
        _config_payload(value, name)

    target = Path(root).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".staging")
    if target.exists() or staging.exists():
        raise FileExistsError("Refusing to overwrite an existing experiment or staging directory")
    staging.mkdir(parents=True)

    artifacts: list[dict[str, Any]] = []
    try:
        common_frames = {
            "fold_table.parquet": run.fold_table,
            "oos_daily.parquet": run.oos_daily,
            "oos_orders.parquet": run.oos_orders,
            "oos_holdings.parquet": run.oos_holdings,
            "oos_pending_targets.parquet": run.oos_pending_targets,
            "benchmark_oos_daily.parquet": run.benchmark_oos_daily,
            "benchmark_oos_orders.parquet": run.benchmark_oos_orders,
            "benchmark_oos_holdings.parquet": run.benchmark_oos_holdings,
            "benchmark_oos_pending_targets.parquet": run.benchmark_oos_pending_targets,
        }
        for filename, frame in common_frames.items():
            path = staging / filename
            frame.write_parquet(path)
            artifacts.append(_record(path, staging))

        fold_manifests = []
        for index, fold in enumerate(run.folds, start=1):
            if not fold.training_admission.admitted or not fold.final_admission.admitted:
                raise ValueError(f"{fold.fold.fold_id} contains a failed training admission")
            if fold.model.acceptance_report_id != fold.provenance.get("acceptance_report_id"):
                raise ValueError(f"{fold.fold.fold_id} model acceptance identity mismatch")
            if fold.model.training_cutoff != fold.fold.test_start:
                raise ValueError(f"{fold.fold.fold_id} model training cutoff is not test_start")
            folder = staging / "folds" / fold.fold.fold_id
            folder.mkdir(parents=True)
            frames = {
                "validation_predictions.parquet": fold.validation_predictions,
                "test_predictions.parquet": fold.test_predictions,
                "model_targets.parquet": fold.targets,
                "daily.parquet": fold.daily,
                "orders.parquet": fold.orders,
                "holdings.parquet": fold.holdings,
                "pending_targets.parquet": fold.pending_targets,
                "benchmark_targets.parquet": fold.benchmark_targets,
                "benchmark_daily.parquet": fold.benchmark_daily,
                "benchmark_orders.parquet": fold.benchmark_orders,
                "benchmark_holdings.parquet": fold.benchmark_holdings,
                "benchmark_pending_targets.parquet": fold.benchmark_pending_targets,
            }
            for filename, frame in frames.items():
                path = folder / filename
                frame.write_parquet(path)
                artifacts.append(_record(path, staging))

            model_path = fold.model.save(folder / "model.txt")
            artifacts.append(_record(model_path, staging))
            metadata_path = model_path.with_suffix(model_path.suffix + ".json")
            artifacts.append(_record(metadata_path, staging))

            fold_manifest_path = folder / "fold.json"
            _write_json(fold_manifest_path, {
                "fold": fold.fold,
                "training_admission": fold.training_admission.to_dict(),
                "final_admission": fold.final_admission.to_dict(),
                "final_training_rows": fold.final_training_rows,
                "model": {
                    "acceptance_report_id": fold.model.acceptance_report_id,
                    "training_cutoff": fold.model.training_cutoff,
                    "training_rows": fold.model.training_rows,
                    "feature_columns": fold.model.feature_columns,
                    "source_fingerprint": fold.model.source_fingerprint,
                    "feature_fingerprint": fold.model.feature_fingerprint,
                    "label_fingerprint": fold.model.label_fingerprint,
                    "model_path": model_path.relative_to(staging).as_posix(),
                    "metadata_path": metadata_path.relative_to(staging).as_posix(),
                },
                "report": fold.report,
                "benchmark_report": fold.benchmark_report,
                "provenance": fold.provenance,
            })
            artifacts.append(_record(fold_manifest_path, staging))
            fold_manifests.append({
                "fold_id": fold.fold.fold_id,
                "manifest_path": fold_manifest_path.relative_to(staging).as_posix(),
                "model_path": model_path.relative_to(staging).as_posix(),
                "model_metadata_path": metadata_path.relative_to(staging).as_posix(),
                "test_start": fold.fold.test_start,
                "test_end": fold.fold.test_end,
            })

        first = run.folds[0]
        manifest = {
            "schema_version": _STORE_SCHEMA_VERSION,
            "experiment_type": "walk_forward_cross_sectional_model",
            "experiment_id": experiment_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repository_commit": repository_commit,
            "acceptance_report_id": first.model.acceptance_report_id,
            "universe_profile": first.provenance.get("universe_profile"),
            "source_fingerprint": first.model.source_fingerprint,
            "feature_columns": list(first.model.feature_columns),
            "top_n": top_n,
            "walk_forward_config": walk_forward_config,
            "model_config": model_config,
            "backtest_config": backtest_config,
            "benchmark_config": benchmark_config,
            "fold_count": len(run.folds),
            "folds": fold_manifests,
            "aggregate_report": run.aggregate_report,
            "model_training_performed": True,
            "training_dataset_unlocked": False,
            "artifacts": artifacts,
        }
        _write_json(staging / "manifest.json", manifest)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def verify_walk_forward_model_run(root: str | Path) -> dict[str, Any]:
    """Verify every persisted artifact hash and the model metadata contract."""
    directory = Path(root).expanduser()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Model experiment manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != _STORE_SCHEMA_VERSION:
        raise ValueError("Unsupported model experiment manifest schema")
    if manifest.get("experiment_type") != "walk_forward_cross_sectional_model":
        raise ValueError("Unexpected experiment type")
    if manifest.get("model_training_performed") is not True:
        raise ValueError("Manifest does not confirm model training")
    if manifest.get("training_dataset_unlocked") is not False:
        raise ValueError("Manifest changed the training lock state")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Manifest has no artifact inventory")

    verified: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("Manifest contains a malformed artifact record")
        relative = Path(str(item.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Manifest contains an unsafe artifact path")
        path = directory / relative
        if not path.is_file():
            raise FileNotFoundError(f"Experiment artifact is missing: {relative.as_posix()}")
        if _sha256(path) != item.get("sha256"):
            raise ValueError(f"Experiment artifact checksum mismatch: {relative.as_posix()}")
        verified.append(relative.as_posix())

    for fold in manifest.get("folds", []):
        metadata_relative = Path(fold["model_metadata_path"])
        metadata = json.loads((directory / metadata_relative).read_text(encoding="utf-8"))
        if metadata.get("admission_accepted") is not True:
            raise ValueError(f"{fold['fold_id']} model metadata lacks accepted admission")
        if metadata.get("training_performed") is not True:
            raise ValueError(f"{fold['fold_id']} model metadata lacks training evidence")
        if metadata.get("acceptance_report_id") != manifest.get("acceptance_report_id"):
            raise ValueError(f"{fold['fold_id']} model acceptance report mismatch")

    return {
        "verified": True,
        "experiment_id": manifest["experiment_id"],
        "experiment_type": manifest["experiment_type"],
        "fold_count": manifest["fold_count"],
        "verified_artifact_count": len(verified),
        "acceptance_report_id": manifest["acceptance_report_id"],
        "repository_commit": manifest["repository_commit"],
        "model_training_performed": True,
        "training_dataset_unlocked": False,
        "manifest": manifest,
    }
