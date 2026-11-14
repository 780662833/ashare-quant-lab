"""Read-only inspection of CNEquity's local instrument master.

This is diagnostic only. It does not qualify listing dates for PIT use and
never unlocks model training or historical backtests.
"""
from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

BASELINE_INSTRUMENT_FIELDS = {"symbol", "list_date", "delist_date"}
TEMPORAL_EVIDENCE_FIELDS = (
    "available_at",
    "source_published_at",
    "observed_at",
    "revision_id",
    "announcement_id",
    "document_id",
    "record_locator",
    "source_url",
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _field_summary(frame: pl.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, dtype in frame.schema.items():
        series = frame.get_column(name)
        rows.append({
            "name": name,
            "dtype": str(dtype),
            "null_count": series.null_count(),
            "non_null_count": frame.height - series.null_count(),
            "distinct_count": series.n_unique(),
        })
    return rows


def _distribution(frame: pl.DataFrame, column: str, *, limit: int = 50) -> list[dict[str, Any]]:
    if column not in frame.columns or frame.is_empty():
        return []
    return (
        frame.group_by(column)
        .agg(pl.len().alias("rows"))
        .sort(column, nulls_last=True)
        .head(limit)
        .to_dicts()
    )


def _date_summary(frame: pl.DataFrame, column: str) -> dict[str, Any] | None:
    if column not in frame.columns:
        return None
    original = frame.get_column(column)
    try:
        parsed = frame.select(pl.col(column).cast(pl.Date, strict=False).alias("_date"))["_date"]
    except Exception as exc:
        return {
            "present": True,
            "dtype": str(original.dtype),
            "parse_error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "present": True,
        "dtype": str(original.dtype),
        "null_count": original.null_count(),
        "unparseable_non_null_count": int(
            (original.is_not_null() & parsed.is_null()).sum()
        ),
        "min": _iso(parsed.min()),
        "max": _iso(parsed.max()),
    }


def _symbol_diagnostics(frame: pl.DataFrame) -> dict[str, Any]:
    if "symbol" not in frame.columns:
        return {"available": False}
    symbols = frame.get_column("symbol").cast(pl.String)
    valid = symbols.str.contains(r"^\d{6}\.(SH|SZ|BJ)$").fill_null(False)
    invalid = frame.filter(~valid)
    suffix = symbols.str.extract(r"\.([A-Z]+)$", 1).fill_null("<MISSING>")
    result: dict[str, Any] = {
        "available": True,
        "rows": frame.height,
        "unique_symbols": symbols.n_unique(),
        "invalid_format_rows": invalid.height,
        "invalid_format_sample": invalid.get_column("symbol").cast(pl.String).head(20).to_list(),
        "exchange_suffix_counts": (
            pl.DataFrame({"suffix": suffix})
            .group_by("suffix")
            .agg(pl.len().alias("rows"))
            .sort("suffix")
            .to_dicts()
        ),
    }
    if frame.height and symbols.null_count() == 0:
        duplicates = (
            frame.group_by("symbol")
            .agg(pl.len().alias("rows"))
            .filter(pl.col("rows") > 1)
            .sort("symbol")
        )
        result["duplicate_symbol_groups"] = duplicates.height
        result["duplicate_symbol_sample"] = duplicates.head(20).to_dicts()
    else:
        result["duplicate_symbol_groups"] = None
        result["duplicate_symbol_sample"] = []
    if "exchange" in frame.columns:
        exchange = frame.get_column("exchange").cast(pl.String).str.to_uppercase()
        known = exchange.is_in(["SH", "SZ", "BJ"]).fill_null(False)
        conflict = frame.filter(known & (exchange != suffix))
        result["exchange_suffix_conflict_rows"] = conflict.height
        result["exchange_suffix_conflict_sample"] = (
            conflict.select("symbol", "exchange").head(20).to_dicts()
        )
    return result


def _date_anomalies(frame: pl.DataFrame, as_of: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "list_date": _date_summary(frame, "list_date"),
        "delist_date": _date_summary(frame, "delist_date"),
    }
    if "list_date" not in frame.columns:
        result["list_date_after_delist_date_rows"] = None
        result["future_list_date_rows"] = None
        return result
    try:
        listed = pl.col("list_date").cast(pl.Date, strict=False)
        future = frame.filter(listed > pl.lit(as_of))
        result["future_list_date_rows"] = future.height
        result["future_list_date_sample"] = (
            future.select("symbol", "list_date").head(20).to_dicts()
            if "symbol" in frame.columns else future.select("list_date").head(20).to_dicts()
        )
        if "delist_date" in frame.columns:
            delisted = pl.col("delist_date").cast(pl.Date, strict=False)
            conflicts = frame.filter(
                listed.is_not_null() & delisted.is_not_null() & (listed > delisted)
            )
            result["list_date_after_delist_date_rows"] = conflicts.height
            result["list_date_after_delist_date_sample"] = (
                conflicts.select("symbol", "list_date", "delist_date").head(20).to_dicts()
                if "symbol" in frame.columns
                else conflicts.select("list_date", "delist_date").head(20).to_dicts()
            )
        else:
            result["list_date_after_delist_date_rows"] = None
    except Exception as exc:
        result["date_order_check_error"] = f"{type(exc).__name__}: {exc}"
        result["list_date_after_delist_date_rows"] = None
        result["future_list_date_rows"] = None
    return result


def _contract_metadata(dataset: str) -> dict[str, Any]:
    """Read semantics from the installed CNEquity registry."""
    try:
        from cnequity.domain.datasets import DATASETS, history_mode_for

        spec = DATASETS.get(dataset)
        if spec is None:
            return {"available": False, "error": f"{dataset!r} is not in DATASETS"}
        return {
            "available": True,
            "pit": getattr(spec, "pit", None),
            "pit_grade": getattr(spec, "pit_grade", None),
            "availability_col": getattr(spec, "availability_col", None),
            "history_mode": history_mode_for(spec),
            "fetch_semantics": getattr(spec, "fetch_semantics", None),
            "primary_source": getattr(spec, "primary_source", None),
            "backup_source": getattr(spec, "backup_source", None),
            "backfill_source": getattr(spec, "backfill_source", None),
            "schema_version": getattr(spec, "schema_version", None),
        }
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _receipt_summary(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        return {"available": False}
    return {
        "available": True,
        "receipt_id": receipt.get("receipt_id"),
        "dataset": receipt.get("dataset"),
        "replayable": receipt.get("replayable"),
        "unpinned_datasets": receipt.get("unpinned_datasets", []),
        "dependencies": receipt.get("dependencies", {}),
        "coverage": receipt.get("coverage", {}),
        "provenance": receipt.get("provenance", {}),
    }


def _summarize_dataset(
    dataset: str,
    frame: pl.DataFrame,
    receipt: Any,
    contract_schema: Mapping[str, Any] | None,
    *,
    as_of: date,
) -> dict[str, Any]:
    expected_fields = set(contract_schema or {})
    if dataset == "instruments" and not expected_fields:
        expected_fields = set(BASELINE_INSTRUMENT_FIELDS)
    observed_fields = set(frame.columns)
    result: dict[str, Any] = {
        "status": "READ" if frame.height else "EMPTY",
        "row_count": frame.height,
        "column_count": len(frame.columns),
        "columns": _field_summary(frame),
        "contract": _contract_metadata(dataset),
        "contract_schema_fields": sorted(expected_fields),
        "missing_contract_fields": sorted(expected_fields - observed_fields),
        "unexpected_observed_fields": sorted(observed_fields - expected_fields)
        if expected_fields else [],
        "receipt": _receipt_summary(receipt),
    }
    for column in ("exchange", "asset_type", "source", "data_version"):
        result[f"{column}_counts"] = _distribution(frame, column)
    if dataset == "instruments":
        result["symbols"] = _symbol_diagnostics(frame)
        result["dates"] = _date_anomalies(frame, as_of)
        result["temporal_evidence_columns"] = {
            field: {
                "present": field in frame.columns,
                "non_null_count": (
                    frame.get_column(field).len() - frame.get_column(field).null_count()
                    if field in frame.columns else 0
                ),
            }
            for field in TEMPORAL_EVIDENCE_FIELDS
        }
        result["listing_date_admission"] = {
            "status": "NOT_ADMITTED",
            "pit_contract": result["contract"].get("pit"),
            "pit_grade": result["contract"].get("pit_grade"),
            "reason": (
                "A current instruments row and list_date do not prove when the listing "
                "fact was first publicly knowable. This diagnostic does not authenticate "
                "an exchange announcement, source publication timestamp, or historical "
                "revision chain. Do not use list_date as a historical PIT feature."
            ),
        }
    return result


def inspect_cnequity_metadata(
    data_root: str | Path,
    *,
    loader: Callable[..., Any] | None = None,
    schema_reader: Callable[[str], Mapping[str, Any]] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Inspect local instruments and delisting metadata through CNEquity's API.

    require_replayable=False is intentional for diagnosis: legacy/unpinned
    local rows remain visible, while their receipts are explicitly labeled.
    """
    if loader is None:
        from cnequity.query import load_with_receipt

        loader = load_with_receipt
    if schema_reader is None:
        from cnequity.query import dataset_schema

        schema_reader = dataset_schema

    root = Path(data_root).expanduser().resolve()
    inspection_date = as_of or datetime.now(timezone.utc).date()
    try:
        installed_version = importlib.metadata.version("cnequity")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None

    report: dict[str, Any] = {
        "schema_version": 1,
        "check": "cnequity_instruments_metadata_inspection",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(root),
        "as_of_date": inspection_date.isoformat(),
        "installed_cnequity_version": installed_version,
        "read_only": True,
        "network_access": False,
        "data_lake_write": False,
        "backfill_started": False,
        "model_fit_started": False,
        "backtest_started": False,
        "historical_universe_admitted": False,
        "datasets": {},
        "cross_checks": {},
        "limitations": [
            "This is a current metadata snapshot inspection, not a historical-universe acceptance.",
            "Receipt completeness=not_assessed is not promoted to complete.",
            "A non-replayable receipt may describe readable rows but cannot pin their source revision.",
            "Listing-date PIT eligibility remains NOT_ADMITTED until event publication and revision evidence are independently audited.",
            "No daily-bar history is loaded, and no backfill, model fit, or empirical backtest is run.",
        ],
    }

    frames: dict[str, pl.DataFrame] = {}
    for dataset in ("instruments", "delisting_events"):
        schema = None
        schema_error = None
        try:
            schema = schema_reader(dataset)
        except Exception as exc:
            schema_error = f"{type(exc).__name__}: {exc}"
        try:
            result = loader(dataset, data_root=str(root), require_replayable=False)
            frame = getattr(result, "frame", None)
            receipt = getattr(result, "receipt", None)
            if not isinstance(frame, pl.DataFrame):
                raise TypeError(f"CNEquity {dataset} read did not return a Polars DataFrame")
            frames[dataset] = frame
            summary = _summarize_dataset(
                dataset, frame, receipt, schema, as_of=inspection_date
            )
            if schema_error is not None:
                summary["contract_schema_error"] = schema_error
            report["datasets"][dataset] = summary
        except Exception as exc:
            report["datasets"][dataset] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "contract": _contract_metadata(dataset),
                "contract_schema_error": schema_error,
            }

    instruments = frames.get("instruments")
    delistings = frames.get("delisting_events")
    if instruments is not None and delistings is not None:
        if "symbol" in instruments.columns and "symbol" in delistings.columns:
            master_symbols = set(
                instruments.get_column("symbol").drop_nulls().cast(pl.String).to_list()
            )
            event_symbols = set(
                delistings.get_column("symbol").drop_nulls().cast(pl.String).to_list()
            )
            missing_master = sorted(event_symbols - master_symbols)
            report["cross_checks"]["delisting_event_symbols"] = {
                "event_unique_symbols": len(event_symbols),
                "symbols_present_in_instruments": len(event_symbols & master_symbols),
                "symbols_missing_from_instruments": len(missing_master),
                "missing_from_instruments_sample": missing_master[:30],
                "note": (
                    "This is a membership cross-check only; delisting_events is not "
                    "substituted for listing-event publication evidence."
                ),
            }

    report["overall_read_status"] = (
        "READ" if report["datasets"].get("instruments", {}).get("status") == "READ"
        else report["datasets"].get("instruments", {}).get("status", "ERROR")
    )
    return report
