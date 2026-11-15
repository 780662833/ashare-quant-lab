"""Receipt-backed assembly of CNEquity reads into the canonical research bundle."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math
from typing import Any

import polars as pl

from ashare_quant.data.cne_adapter import CNEquityAdapter
from ashare_quant.data.execution_evidence import (
    combine_execution_evidence,
    normalize_cnequity_trading_status,
)
from ashare_quant.exceptions import ResearchBundleError
from ashare_quant.research.bundle import ResearchDataBundle, build_research_data_bundle
from ashare_quant.backtest.engine import weekly_rebalance_dates

_RAW_COLUMNS = ("instrument", "date", "open", "high", "low", "close", "volume", "amount")
_ADJUSTED_COLUMNS = ("instrument", "date", "adj_close", "adj_is_exact")
_PIT_AVAILABLE_AT_POLICY = "announce_date_end_of_day_Asia/Shanghai"


def _adapter_identity(adapter: CNEquityAdapter, dataset: str) -> dict[str, Any]:
    revisions = adapter.source_revision_map
    fingerprints = adapter.source_contract_fingerprints
    revision_id = revisions.get(dataset)
    fingerprint = fingerprints.get(dataset)
    if revision_id is None or not str(revision_id).strip():
        raise ResearchBundleError(f"CNEquity adapter has no pinned revision for {dataset}")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ResearchBundleError(f"CNEquity adapter has no contract fingerprint for {dataset}")
    return {"revision_id": revision_id, "contract_fingerprint": fingerprint}


def assemble_cnequity_research_bundle(
    adapter: CNEquityAdapter,
    *,
    start_date: str,
    end_date: str,
    acceptance_report: Mapping[str, Any],
    acceptance_report_id: str,
    price_limit_evidence: pl.DataFrame,
    price_limit_revision: Mapping[str, Any],
    coverage_audits: Mapping[str, Mapping[str, Any]],
) -> ResearchDataBundle:
    """Read, receipt-pin and assemble canonical market/execution sources."""
    if not isinstance(adapter, CNEquityAdapter):
        raise TypeError("adapter must be a CNEquityAdapter")
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")
    if not isinstance(acceptance_report, Mapping):
        raise TypeError("acceptance_report must be a mapping")
    if not isinstance(price_limit_evidence, pl.DataFrame):
        raise TypeError("price_limit_evidence must be a Polars DataFrame")
    if not isinstance(price_limit_revision, Mapping):
        raise TypeError("price_limit_revision must be a structured identity mapping")

    sessions = adapter.trading_calendar(start_date, end_date)
    if len(sessions) < 2:
        raise ResearchBundleError("CNEquity trading calendar has fewer than two sessions")
    bars = adapter.daily_bars(None, start_date, end_date)
    missing_raw = set(_RAW_COLUMNS).difference(bars.columns)
    missing_adjusted = set(_ADJUSTED_COLUMNS).difference(bars.columns)
    if missing_raw or missing_adjusted:
        raise ResearchBundleError(
            "CNEquity daily_bars missing canonical fields: "
            f"raw={sorted(missing_raw)}, adjusted={sorted(missing_adjusted)}"
        )
    raw_bars = bars.select(*_RAW_COLUMNS)
    adjusted_bars = bars.select(*_ADJUSTED_COLUMNS)
    instruments = sorted(raw_bars["instrument"].unique().to_list())
    if not instruments:
        raise ResearchBundleError("CNEquity daily_bars returned no instruments")

    membership_rows: list[dict[str, str]] = []
    for signal_date in weekly_rebalance_dates(sessions):
        snapshot = adapter.point_in_time_universe(signal_date)
        if not isinstance(snapshot, pl.DataFrame) or "instrument" not in snapshot.columns:
            raise ResearchBundleError(
                f"CNEquity point-in-time universe for {signal_date} lacks instrument column"
            )
        if snapshot.is_empty() or snapshot["instrument"].null_count():
            raise ResearchBundleError(
                f"CNEquity point-in-time universe for {signal_date} is empty or has null symbols"
            )
        membership_rows.extend(
            {"instrument": str(symbol), "date": signal_date}
            for symbol in sorted(set(snapshot["instrument"].cast(pl.String).to_list()))
        )
    if not membership_rows:
        raise ResearchBundleError("CNEquity adapter returned no weekly universe snapshots")
    membership = pl.DataFrame(membership_rows)

    native_status = adapter.trading_status(instruments, start_date, end_date)
    normalized_status = normalize_cnequity_trading_status(native_status)
    execution_evidence = combine_execution_evidence(
        raw_bars, normalized_status, price_limit_evidence
    )

    daily_identity = _adapter_identity(adapter, "daily_bars")
    source_revisions = {
        "daily_bars": daily_identity,
        "adj_factors": _adapter_identity(adapter, "adj_factors"),
        "universe": dict(daily_identity),
        "trading_status": _adapter_identity(adapter, "trading_status"),
        "trading_calendar": _adapter_identity(adapter, "trading_calendar"),
        "price_limit_rules": dict(price_limit_revision),
    }
    receipts = adapter.source_receipts
    if not receipts:
        raise ResearchBundleError("CNEquity adapter produced no source receipts")

    return build_research_data_bundle(
        raw_bars=raw_bars,
        adjusted_bars=adjusted_bars,
        universe_membership=membership,
        execution_evidence=execution_evidence,
        trading_dates=sessions,
        acceptance_report=acceptance_report,
        acceptance_report_id=acceptance_report_id,
        source_revisions=source_revisions,
        source_receipts=receipts,
        coverage_audits=coverage_audits,
        start_date=start_date,
        end_date=end_date,
        universe_profile=adapter.profile,
    )


def bind_pit_fundamental_provenance(
    bundle: ResearchDataBundle,
    *,
    items: list[str],
    as_of_date: str,
    receipt: Mapping[str, Any],
    revision_identity: Mapping[str, Any],
    row_count: int,
) -> None:
    """Bind one strict PIT adapter receipt/revision to a validated bundle."""
    if not isinstance(bundle, ResearchDataBundle):
        raise TypeError("bundle must be a ResearchDataBundle")
    bundle._require_validated()
    parsed = date.fromisoformat(as_of_date)
    if parsed.isoformat() != as_of_date:
        raise ValueError("as_of_date must use canonical YYYY-MM-DD")
    if not items or len(set(items)) != len(items):
        raise ValueError("items must be non-empty and unique")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
        raise ValueError("row_count must be a positive integer")
    if not isinstance(receipt, Mapping) or receipt.get("replayable") is not True:
        raise ResearchBundleError("Strict PIT adapter receipt must be replayable")
    if not isinstance(revision_identity, Mapping):
        raise ResearchBundleError("Strict PIT revision identity must be a mapping")
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, (str, int)) or not str(receipt_id).strip():
        raise ResearchBundleError("Strict PIT adapter receipt requires receipt_id")
    dependencies = receipt.get("dependencies")
    if not isinstance(dependencies, Mapping) or "financial_statement_items" not in dependencies:
        raise ResearchBundleError("Strict PIT receipt must pin financial_statement_items")
    pit_dependency = dependencies["financial_statement_items"]
    if not isinstance(pit_dependency, Mapping):
        raise ResearchBundleError("Strict PIT receipt financial_statement_items identity is malformed")
    normalized_identity = {
        "revision_id": revision_identity.get("revision_id"),
        "contract_fingerprint": revision_identity.get("contract_fingerprint"),
    }
    if (
        not isinstance(normalized_identity["revision_id"], (str, int))
        or isinstance(normalized_identity["revision_id"], bool)
        or not str(normalized_identity["revision_id"]).strip()
        or not isinstance(normalized_identity["contract_fingerprint"], str)
        or not normalized_identity["contract_fingerprint"].strip()
    ):
        raise ResearchBundleError("Strict PIT revision identity is incomplete")
    if (
        pit_dependency.get("revision_id") != normalized_identity["revision_id"]
        or pit_dependency.get("contract_fingerprint") != normalized_identity["contract_fingerprint"]
    ):
        raise ResearchBundleError("Strict PIT receipt and revision identity do not match")

    provenance = bundle.provenance
    source_receipts = list(provenance.get("source_receipts", []))
    if any(item.get("dataset") == "financial_statement_items" for item in source_receipts):
        raise ResearchBundleError("Strict PIT receipt is already bound to this bundle")
    source_receipts.append({
        "dataset": "financial_statement_items",
        "receipt": dict(receipt),
    })

    observed = dict(provenance.get("receipt_provenance", {}).get("dependency_identities", {}))
    for name, identity in dependencies.items():
        if not isinstance(identity, Mapping):
            raise ResearchBundleError(f"Strict PIT dependency identity is malformed: {name}")
        normalized = {
            "revision_id": identity.get("revision_id"),
            "contract_fingerprint": identity.get("contract_fingerprint"),
        }
        if (
            not isinstance(normalized["revision_id"], (str, int))
            or isinstance(normalized["revision_id"], bool)
            or not str(normalized["revision_id"]).strip()
            or not isinstance(normalized["contract_fingerprint"], str)
            or not normalized["contract_fingerprint"].strip()
        ):
            raise ResearchBundleError(f"Strict PIT dependency identity is incomplete: {name}")
        if name in observed and observed[name] != normalized:
            raise ResearchBundleError(f"Strict PIT dependency revision drift: {name}")
        observed[name] = normalized

    source_revisions = dict(provenance.get("source_revisions", {}))
    source_revisions["financial_statement_items"] = normalized_identity
    receipt_provenance = dict(provenance.get("receipt_provenance", {}))
    receipt_provenance["dependency_identities"] = observed
    receipt_ids = set(receipt_provenance.get("adapter_receipt_ids", []))
    receipt_ids.add(str(receipt_id))
    receipt_provenance["adapter_receipt_ids"] = sorted(receipt_ids)
    completeness = dict(receipt_provenance.get("coverage_completeness", {}))
    completeness["financial_statement_items"] = (
        receipt.get("coverage", {}).get("completeness", "receipt_only")
        if isinstance(receipt.get("coverage"), Mapping) else "receipt_only"
    )
    receipt_provenance["coverage_completeness"] = completeness

    provenance["source_receipts"] = source_receipts
    provenance["source_revisions"] = source_revisions
    provenance["receipt_provenance"] = receipt_provenance
    provenance["pit_fundamentals"] = {
        "items": sorted(items),
        "as_of_date": as_of_date,
        "receipt_id": str(receipt_id),
        "source_revision_identity": normalized_identity,
        "availability_policy": _PIT_AVAILABLE_AT_POLICY,
        "row_count": row_count,
    }


def assemble_cnequity_pit_fundamentals(
    adapter: CNEquityAdapter,
    bundle: ResearchDataBundle,
    *,
    items: list[str],
    as_of_date: str,
) -> pl.DataFrame:
    """Read and normalize strict PIT facts for the feature builder.

    CNEquity announce_date is treated conservatively as available at 23:59:59
    Asia/Shanghai on that date. A fact announced on a signal date therefore
    cannot enter that same day's 16:00 feature snapshot.
    """
    if not isinstance(adapter, CNEquityAdapter):
        raise TypeError("adapter must be a CNEquityAdapter")
    if not isinstance(bundle, ResearchDataBundle):
        raise TypeError("bundle must be a ResearchDataBundle")
    bundle._require_validated()
    if not items or len(set(items)) != len(items):
        raise ValueError("items must be non-empty and unique")
    report_window = bundle.provenance.get("acceptance_report_window", {})
    if as_of_date != report_window.get("end"):
        raise ResearchBundleError("PIT as_of_date must equal the validated bundle end date")
    instruments = sorted(bundle.universe_membership["instrument"].unique().to_list())
    frame = adapter.financial_statement_items(items, as_of_date, instruments=instruments)
    required = {
        "symbol", "report_period", "item_code", "item_value", "announce_date",
        "pit_quality", "pit_is_exact",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ResearchBundleError(f"Strict PIT result missing canonical fields: {sorted(missing)}")
    if frame.is_empty():
        raise ResearchBundleError("Strict PIT query returned no financial facts")

    rows: list[dict[str, Any]] = []
    for row in frame.select(sorted(required)).to_dicts():
        instrument = str(row["symbol"])
        item = str(row["item_code"])
        if instrument not in instruments or item not in items:
            raise ResearchBundleError("Strict PIT result contains an unrequested symbol or item")
        if row["pit_quality"] != "strict" or row["pit_is_exact"] is not True:
            raise ResearchBundleError("Strict PIT result contains non-strict or non-exact facts")
        if row["report_period"] is None or not str(row["report_period"]).strip():
            raise ResearchBundleError("Strict PIT result contains an empty report_period")
        if row["announce_date"] is None:
            raise ResearchBundleError("Strict PIT result contains a null announce_date")
        announced = row["announce_date"]
        announced_text = announced.isoformat() if hasattr(announced, "isoformat") else str(announced)
        announced_date = announced_text[:10]
        try:
            parsed_announce = date.fromisoformat(announced_date)
        except ValueError as exc:
            raise ResearchBundleError("Strict PIT announce_date is not a valid date") from exc
        if parsed_announce.isoformat() != announced_date or announced_date > as_of_date:
            raise ResearchBundleError("Strict PIT fact announcement date is outside as_of window")
        if isinstance(row["item_value"], bool):
            raise ResearchBundleError("Strict PIT item_value must be numeric, not boolean")
        try:
            value = float(row["item_value"])
        except (TypeError, ValueError) as exc:
            raise ResearchBundleError("Strict PIT item_value must be numeric") from exc
        if not math.isfinite(value):
            raise ResearchBundleError("Strict PIT item_value must be finite")
        rows.append({
            "instrument": instrument,
            "item": item,
            "report_period": str(row["report_period"]),
            "available_at": f"{announced_date}T23:59:59+08:00",
            "value": value,
            "pit_quality": "strict",
            "pit_is_exact": True,
        })

    normalized = pl.DataFrame(
        rows,
        schema={
            "instrument": pl.String,
            "item": pl.String,
            "report_period": pl.String,
            "available_at": pl.String,
            "value": pl.Float64,
            "pit_quality": pl.String,
            "pit_is_exact": pl.Boolean,
        },
    ).sort(["instrument", "item", "available_at", "report_period"])
    duplicate_key = ["instrument", "item", "report_period", "available_at"]
    if normalized.select(pl.struct(duplicate_key).is_duplicated().any()).item():
        raise ResearchBundleError("Strict PIT facts contain duplicate instrument/item/report keys")
    observed_pairs = set(zip(normalized["instrument"].to_list(), normalized["item"].to_list()))
    missing_pairs = {
        (instrument, item) for instrument in instruments for item in items
    }.difference(observed_pairs)
    if missing_pairs:
        raise ResearchBundleError(
            f"Strict PIT facts missing symbol/item coverage: {sorted(missing_pairs)[:5]}"
        )

    receipt_records = adapter.source_receipts
    if not receipt_records or receipt_records[-1].get("dataset") != "financial_statement_items":
        raise ResearchBundleError("CNEquity adapter did not retain the strict PIT source receipt")
    bind_pit_fundamental_provenance(
        bundle,
        items=items,
        as_of_date=as_of_date,
        receipt=receipt_records[-1]["receipt"],
        revision_identity=_adapter_identity(adapter, "financial_statement_items"),
        row_count=normalized.height,
    )
    return normalized
