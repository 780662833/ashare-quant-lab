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
            pl.DataFrame({"suffix": suffix.to_list()})
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



def _listing_date_missingness(frame: pl.DataFrame) -> dict[str, Any]:
    """Attribute null/unparseable listing dates without treating the snapshot as PIT."""
    if "list_date" not in frame.columns:
        return {
            "status": "UNAVAILABLE",
            "reason": "instruments has no list_date column",
            "total_rows": frame.height,
            "missing_or_unparseable_rows": None,
        }

    try:
        parsed_dates = frame.select(
            pl.col("list_date").cast(pl.Date, strict=False).alias("_parsed_list_date")
        ).get_column("_parsed_list_date").to_list()
    except Exception as exc:
        return {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "total_rows": frame.height,
            "missing_or_unparseable_rows": None,
        }

    rows = frame.to_dicts()
    missing_flags = [value is None for value in parsed_dates]

    def grouped(dimension: str) -> dict[str, Any]:
        if dimension not in frame.columns:
            return {
                "available": False,
                "missing_column": dimension,
                "groups": [],
            }
        buckets: dict[str, dict[str, Any]] = {}
        for row, is_missing in zip(rows, missing_flags, strict=True):
            raw_value = row.get(dimension)
            label = "<NULL>" if raw_value is None else str(raw_value)
            bucket = buckets.setdefault(
                label,
                {"value": label, "rows": 0, "missing_list_date_rows": 0},
            )
            bucket["rows"] += 1
            if is_missing:
                bucket["missing_list_date_rows"] += 1
        groups = []
        for label in sorted(buckets):
            bucket = buckets[label]
            bucket["non_missing_list_date_rows"] = (
                bucket["rows"] - bucket["missing_list_date_rows"]
            )
            bucket["missing_rate"] = (
                bucket["missing_list_date_rows"] / bucket["rows"]
                if bucket["rows"] else 0.0
            )
            groups.append(bucket)
        return {"available": True, "dimension": dimension, "groups": groups}

    def serialize_record(row: Mapping[str, Any]) -> dict[str, Any]:
        fields = (
            "symbol", "name", "exchange", "asset_type", "source",
            "list_date", "delist_date",
        )
        record = {
            field: _iso(row.get(field))
            for field in fields
            if field in frame.columns
        }
        record["list_date_missing_reason"] = (
            "null" if row.get("list_date") is None else "unparseable"
        )
        return record

    asset_type_available = "asset_type" in frame.columns
    symbol_available = "symbol" in frame.columns
    stock_total = 0
    a_share_stock_total = 0
    stock_missing: list[dict[str, Any]] = []
    a_share_stock_missing: list[dict[str, Any]] = []
    unqualified_stock_missing: list[dict[str, Any]] = []
    non_stock_missing = 0
    unclassified_missing = 0

    for row, is_missing in zip(rows, missing_flags, strict=True):
        asset_type = row.get("asset_type")
        is_stock = (
            asset_type is not None
            and str(asset_type).strip().lower() == "stock"
        )
        symbol = row.get("symbol")
        symbol_is_exchange_qualified = (
            isinstance(symbol, str)
            and len(symbol) == 9
            and symbol[:6].isdigit()
            and symbol[6:] in {".SH", ".SZ", ".BJ"}
        )
        reported_exchange = row.get("exchange")
        exchange_matches = (
            symbol_is_exchange_qualified
            and (
                reported_exchange is None
                or str(reported_exchange).strip().upper() == symbol[7:]
            )
        )
        if is_stock:
            stock_total += 1
            if exchange_matches:
                a_share_stock_total += 1

        if not is_missing:
            continue
        if asset_type is None:
            unclassified_missing += 1
            continue
        if not is_stock:
            non_stock_missing += 1
            continue

        record = serialize_record(row)
        stock_missing.append(record)
        if exchange_matches:
            a_share_stock_missing.append(record)
        else:
            unqualified_stock_missing.append(record)

    missing_count = sum(missing_flags)
    return {
        "status": "AVAILABLE",
        "total_rows": frame.height,
        "missing_or_unparseable_rows": missing_count,
        "missing_rate": missing_count / frame.height if frame.height else 0.0,
        "asset_type_available": asset_type_available,
        "symbol_available": symbol_available,
        "missing_by_asset_type": grouped("asset_type"),
        "missing_by_exchange": grouped("exchange"),
        "missing_by_source": grouped("source"),
        "stock_rows": stock_total,
        "stock_missing_list_date_rows": len(stock_missing),
        "stock_missing_rate": len(stock_missing) / stock_total if stock_total else 0.0,
        "a_share_stock_rows": a_share_stock_total,
        "a_share_stock_missing_list_date_rows": len(a_share_stock_missing),
        "a_share_stock_missing_rate": (
            len(a_share_stock_missing) / a_share_stock_total
            if a_share_stock_total else 0.0
        ),
        "unqualified_stock_missing_list_date_rows": len(unqualified_stock_missing),
        "non_stock_missing_list_date_rows": non_stock_missing,
        "unclassified_missing_list_date_rows": unclassified_missing,
        "stock_missing_records": stock_missing,
        "a_share_stock_missing_records": a_share_stock_missing,
        "unqualified_stock_missing_records": unqualified_stock_missing,
    }


def _listing_event_evidence_coverage(
    instruments: pl.DataFrame,
    evidence: pl.DataFrame | None,
    source_identities: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Audit event-evidence coverage for the current stock reference snapshot.

    This is intentionally not a historical-universe completeness assertion.
    Evidence is accepted only after the existing official-document validator
    binds every row to a replayable source receipt and capability audit.
    """
    exchange_specs = (
        ("SSE", ".SH", "sse_listing_events"),
        ("SZSE", ".SZ", "szse_listing_events"),
        ("BSE", ".BJ", "bse_listing_events"),
    )
    expected_by_exchange: dict[str, set[str]] = {
        exchange: set() for exchange, _, _ in exchange_specs
    }
    if {"symbol", "asset_type"}.issubset(instruments.columns):
        for row in instruments.select("symbol", "asset_type").to_dicts():
            symbol = row.get("symbol")
            if (
                str(row.get("asset_type") or "").strip().lower() == "stock"
                and isinstance(symbol, str)
                and len(symbol) == 9
                and symbol[:6].isdigit()
            ):
                for exchange, suffix, _ in exchange_specs:
                    if symbol.endswith(suffix):
                        expected_by_exchange[exchange].add(symbol)
                        break

    expected_count = sum(len(values) for values in expected_by_exchange.values())
    base: dict[str, Any] = {
        "scope": "current instruments stock snapshot only; not historical-universe completeness",
        "expected_stock_symbols": expected_count,
        "source_datasets": [
            {"exchange": exchange, "dataset": dataset}
            for exchange, _, dataset in exchange_specs
        ],
    }

    if evidence is None:
        return {
            **base,
            "status": "NOT_CONNECTED",
            "evidence_rows": None,
            "verified_event_symbols": None,
            "missing_evidence_symbols": None,
            "reason": (
                "No official listing-event evidence frame/source is connected to this "
                "read-only metadata inspection. No exchange event data was inferred "
                "from instruments.list_date."
            ),
            "by_exchange": [
                {
                    "exchange": exchange,
                    "dataset": dataset,
                    "expected_stock_symbols": len(expected_by_exchange[exchange]),
                    "verified_event_symbols": None,
                    "missing_evidence_symbols": None,
                    "status": "NOT_CONNECTED",
                }
                for exchange, _, dataset in exchange_specs
            ],
        }

    if source_identities is None:
        return {
            **base,
            "status": "NOT_VALIDATED",
            "evidence_rows": evidence.height,
            "verified_event_symbols": None,
            "missing_evidence_symbols": None,
            "reason": (
                "Evidence rows were supplied without receipt-derived source identities; "
                "official-document and replayable-receipt validation was not performed."
            ),
        }

    try:
        from ashare_quant.research.listing_event_evidence import (
            validate_listing_event_evidence,
        )

        validated = validate_listing_event_evidence(
            evidence,
            source_identities=source_identities,
        )
    except Exception as exc:
        return {
            **base,
            "status": "INVALID",
            "evidence_rows": evidence.height,
            "verified_event_symbols": None,
            "missing_evidence_symbols": None,
            "validation_error": f"{type(exc).__name__}: {exc}",
        }

    validated_rows = validated.to_dicts()
    event_symbols = {str(row["instrument"]) for row in validated_rows}
    expected_symbols = set().union(*expected_by_exchange.values())
    missing_symbols = sorted(expected_symbols - event_symbols)
    unexpected_symbols = sorted(event_symbols - expected_symbols)

    instrument_dates: dict[str, str | None] = {}
    if {"symbol", "list_date"}.issubset(instruments.columns):
        for row in instruments.select("symbol", "list_date").to_dicts():
            instrument_dates[str(row["symbol"])] = _iso(row.get("list_date"))

    date_mismatches = []
    for row in validated_rows:
        current_date = instrument_dates.get(str(row["instrument"]))
        if current_date is not None and current_date != str(row["listing_date"]):
            date_mismatches.append({
                "instrument": row["instrument"],
                "instruments_list_date": current_date,
                "official_listing_date": str(row["listing_date"]),
            })

    by_exchange = []
    for exchange, suffix, dataset in exchange_specs:
        expected = expected_by_exchange[exchange]
        observed = {
            str(row["instrument"])
            for row in validated_rows
            if row["exchange"] == exchange
        }
        missing = sorted(expected - observed)
        rate = len(expected & observed) / len(expected) if expected else 0.0
        by_exchange.append({
            "exchange": exchange,
            "dataset": dataset,
            "expected_stock_symbols": len(expected),
            "verified_event_symbols": len(observed),
            "covered_expected_symbols": len(expected & observed),
            "missing_evidence_symbols": len(missing),
            "missing_evidence_sample": missing[:20],
            "coverage_rate": rate,
            "status": "PASS" if expected and not missing else "FAIL",
        })

    status = (
        "PASS"
        if expected_symbols
        and not missing_symbols
        and not unexpected_symbols
        and not date_mismatches
        else "FAIL"
    )
    return {
        **base,
        "status": status,
        "evidence_rows": validated.height,
        "verified_event_symbols": len(event_symbols),
        "covered_expected_symbols": len(expected_symbols & event_symbols),
        "missing_evidence_symbols": len(missing_symbols),
        "missing_evidence_sample": missing_symbols[:30],
        "unexpected_evidence_symbols": len(unexpected_symbols),
        "unexpected_evidence_sample": unexpected_symbols[:30],
        "listing_date_mismatches": len(date_mismatches),
        "listing_date_mismatch_sample": date_mismatches[:30],
        "by_exchange": by_exchange,
        "validation": "PASS",
        "note": (
            "PASS means official event evidence covers the current instruments stock "
            "snapshot only. It does not prove historical security-universe completeness "
            "or independently admit a historical backtest."
        ),
    }

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



def _missing_dataset_status(exc: Exception) -> bool:
    """Distinguish an absent dataset from an API or storage failure."""
    return "no parquet data for dataset" in str(exc).lower()


def _dataset_role(dataset: str) -> dict[str, str]:
    if dataset == "instruments":
        return {
            "role": "required_for_historical_universe",
            "next_step": (
                "Inspect the supported instruments backfill plan before any fetch. "
                "Use scripts/plan_cnequity_instruments_backfill.py; it only prints a plan."
            ),
        }
    if dataset == "delisting_events":
        return {
            "role": "derived_supplementary_dataset",
            "next_step": (
                "This is a derived output, not a normal source backfill. Do not run "
                "cne backfill delisting_events. This repository does not provide "
                "scripts/delisted_ops.py; verify the installed CNEquity version's "
                "documented rebuild workflow before considering any rebuild."
            ),
        }
    return {"role": "unknown", "next_step": "Inspect the dataset-specific contract."}


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
        result["listing_date_missingness"] = _listing_date_missingness(frame)
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
    adapter: Any | None = None,
    as_of: date | None = None,
    listing_event_evidence: pl.DataFrame | None = None,
    listing_event_source_identities: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspect local instruments and delisting metadata through CNEquity's API.

    require_replayable=False is intentional for diagnosis: legacy/unpinned
    local rows remain visible, while their receipts are explicitly labeled.
    """
    root = Path(data_root).expanduser().resolve()
    if loader is None or schema_reader is None:
        if adapter is None:
            from ashare_quant.data.cne_adapter import CNEquityAdapter

            adapter = CNEquityAdapter(data_root=root)
        if loader is None:
            loader = adapter.read_diagnostic_dataset
        if schema_reader is None:
            schema_reader = adapter.dataset_schema
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
            "Official listing-event coverage is NOT_CONNECTED unless a validated evidence frame and source identities are supplied.",
            "Current instruments membership and delisting summaries do not prove historical-universe completeness.",
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
            missing = _missing_dataset_status(exc)
            role = _dataset_role(dataset)
            report["datasets"][dataset] = {
                "status": "MISSING" if missing else "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "contract": _contract_metadata(dataset),
                "dataset_role": role["role"],
                "next_step": role["next_step"],
                "contract_schema_error": schema_error,
            }

    instruments = frames.get("instruments")
    delistings = frames.get("delisting_events")
    if instruments is not None:
        report["cross_checks"]["listing_event_evidence"] = (
            _listing_event_evidence_coverage(
                instruments,
                listing_event_evidence,
                listing_event_source_identities,
            )
        )
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
