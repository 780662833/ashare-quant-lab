"""Receipt-backed assembly of CNEquity reads into the canonical research bundle."""
from __future__ import annotations

from collections.abc import Mapping
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
    """Read, receipt-pin and assemble every required source for price-only research.

    Historical limit evidence and its audit are explicit caller-supplied inputs;
    this function never derives price limits or locked states. All CNEquity reads
    are made through the adapter and retained in its ordered source receipts.
    """
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
