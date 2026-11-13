"""CNEquity receipt-backed adapter with fail-closed research evidence checks.

Raw OHLC remains the execution-price source. HFQ columns are research-only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import polars as pl

RESEARCH_PROFILE = "cn_a_sh_sz_research_v1"
PIT_EVIDENCE_COLUMNS = {"pit_is_exact", "pit_quality"}


class CNEquityDataError(RuntimeError):
    """Raised when CNEquity data cannot safely enter a research run."""


@dataclass(slots=True)
class CNEquityAdapter:
    """Read market data and PIT facts from a CNEquity lake."""

    config_path: str | Path | None = None
    data_root: str | Path | None = None
    profile: str = RESEARCH_PROFILE
    _config: Any = field(init=False, default=None, repr=False)
    _source_receipts: list[dict[str, Any]] = field(init=False, default_factory=list, repr=False)
    _revision_map: dict[str, str | int] = field(init=False, default_factory=dict, repr=False)
    _revision_fingerprints: dict[str, str] = field(init=False, default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.config_path is not None and self.data_root is not None:
            raise ValueError("Pass config_path or data_root, not both")
        if self.config_path is None and self.data_root is None:
            raise ValueError("CNEquity config_path or data_root is required")
        self._config = None
        if self.config_path is not None:
            from cnequity.config import load_config
            self._config = load_config(Path(self.config_path).expanduser())

    @property
    def source_receipts(self) -> list[dict[str, Any]]:
        """Return a copy of the replayable source receipts collected by this adapter."""
        return deepcopy(self._source_receipts)

    @property
    def source_revision_map(self) -> dict[str, str | int]:
        """Return the dataset revisions pinned by this adapter session."""
        return dict(self._revision_map)

    @property
    def source_contract_fingerprints(self) -> dict[str, str]:
        """Return contract fingerprints paired with the pinned revisions."""
        return dict(self._revision_fingerprints)

    def _record_receipt(self, dataset: str, receipt: Mapping[str, Any]) -> None:
        """Pin dependency revisions and reject drift across reads in this session.

        CNEquity receipts describe the dependencies of one query, not an
        atomic lake-wide snapshot. New dependencies are pinned when first
        encountered; already-seen dependencies must retain both revision and
        contract identity on every later read.
        """
        dependencies = receipt.get("dependencies")
        if not isinstance(dependencies, Mapping) or not dependencies:
            raise CNEquityDataError(
                f"CNEquity {dataset} receipt is missing dependency identities"
            )
        observed: dict[str, tuple[str | int, str]] = {}
        for name, identity in dependencies.items():
            if not isinstance(identity, Mapping):
                raise CNEquityDataError(
                    f"CNEquity {dataset} receipt has invalid dependency identity: {name}"
                )
            revision_id = identity.get("revision_id")
            fingerprint = identity.get("contract_fingerprint")
            if not isinstance(revision_id, (str, int)) or not str(revision_id):
                raise CNEquityDataError(
                    f"CNEquity {dataset} dependency {name} has no committed revision_id"
                )
            if not isinstance(fingerprint, str) or not fingerprint:
                raise CNEquityDataError(
                    f"CNEquity {dataset} dependency {name} has no contract_fingerprint"
                )
            observed[str(name)] = (revision_id, fingerprint)

        if dataset not in observed:
            raise CNEquityDataError(
                f"CNEquity {dataset} receipt does not identify its primary dataset"
            )

        for name, (revision_id, fingerprint) in observed.items():
            pinned_revision = self._revision_map.get(name)
            pinned_fingerprint = self._revision_fingerprints.get(name)
            if pinned_revision is not None and pinned_revision != revision_id:
                raise CNEquityDataError(
                    f"CNEquity dependency revision drift for {name}: "
                    f"{pinned_revision!r} != {revision_id!r}"
                )
            if pinned_fingerprint is not None and pinned_fingerprint != fingerprint:
                raise CNEquityDataError(
                    f"CNEquity dependency contract drift for {name}"
                )

        for name, (revision_id, fingerprint) in observed.items():
            self._revision_map[name] = revision_id
            self._revision_fingerprints[name] = fingerprint

    def _load(self, dataset: str, **kwargs: Any) -> pl.DataFrame:
        """Read through CNEquity's receipt API; ordinary unprovenanced reads are forbidden."""
        from cnequity.query import load_with_receipt
        if self._config is not None:
            kwargs["config"] = self._config
        else:
            kwargs["data_root"] = str(Path(self.data_root).expanduser())
        kwargs["require_replayable"] = True
        if self._revision_map:
            # CNEquity accepts a per-dataset revision map. Reuse every
            # previously observed dependency revision; newly encountered
            # dependencies are captured by the first receipt that needs them.
            kwargs["revision_map"] = dict(self._revision_map)
        result = load_with_receipt(dataset, **kwargs)
        frame = getattr(result, "frame", None)
        receipt = getattr(result, "receipt", None)
        if not isinstance(frame, pl.DataFrame):
            raise CNEquityDataError(
                f"CNEquity {dataset} receipt read did not return a Polars DataFrame"
            )
        if not isinstance(receipt, Mapping) or receipt.get("replayable") is not True:
            raise CNEquityDataError(
                f"CNEquity {dataset} did not return a replayable source receipt"
            )
        self._record_receipt(dataset, receipt)
        self._source_receipts.append({"dataset": dataset, "receipt": deepcopy(dict(receipt))})
        return frame

    @staticmethod
    def _canonical_bars(frame: pl.DataFrame) -> pl.DataFrame:
        required = {"symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"}
        missing = required.difference(frame.columns)
        if missing:
            raise CNEquityDataError(f"CNEquity daily_bars missing columns: {sorted(missing)}")
        if frame.select(pl.struct(["symbol", "trade_date"]).is_duplicated().any()).item():
            raise CNEquityDataError("CNEquity daily_bars contains duplicate symbol/date keys")
        return frame.rename({"symbol": "instrument", "trade_date": "date"}).sort(
            ["instrument", "date"]
        )

    def daily_bars(
        self, instruments: list[str] | None, start_date: str, end_date: str
    ) -> pl.DataFrame:
        """Return raw execution OHLC and exact HFQ research columns.

        CNEquity's strict_adj proves factor coverage, not historical adjustment
        accuracy; the latter is a separate acceptance check and must not be
        inferred from adj_is_exact.
        """
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        frame = self._load(
            "daily_bars", start=start_date, end=end_date, symbols=instruments,
            adjust="hfq", strict_adj=True, profile=self.profile, strict_universe=True,
        )
        if frame.is_empty():
            raise CNEquityDataError(f"No daily_bars for {start_date}..{end_date}")
        required_adjusted = {"adj_open", "adj_high", "adj_low", "adj_close", "adj_is_exact"}
        missing = required_adjusted.difference(frame.columns)
        if missing:
            raise CNEquityDataError(
                f"HFQ evidence columns missing: {sorted(missing)}; refusing research data"
            )
        if frame["adj_is_exact"].null_count() or not frame["adj_is_exact"].all():
            raise CNEquityDataError("HFQ factor coverage is missing or not exact")
        return self._canonical_bars(frame)

    def point_in_time_universe(self, as_of_date: str) -> pl.DataFrame:
        """Return strict research-eligible SH/SZ securities on one date."""
        frame = self._load(
            "daily_bars", start=as_of_date, end=as_of_date, profile=self.profile,
            strict_universe=True,
        )
        if frame.is_empty():
            raise CNEquityDataError(
                f"No eligible securities on {as_of_date}; check lake coverage/evidence"
            )
        required = {"symbol", "trade_date"}
        missing = required.difference(frame.columns)
        if missing:
            raise CNEquityDataError(f"Universe evidence missing columns: {sorted(missing)}")
        if frame["symbol"].null_count() or frame["trade_date"].null_count():
            raise CNEquityDataError("Universe evidence contains null keys")
        if frame.filter(pl.col("trade_date").cast(pl.String) != as_of_date).height:
            raise CNEquityDataError("Universe query returned rows outside requested as-of date")
        return (
            frame.select(pl.col("symbol").alias("instrument"))
            .unique()
            .sort("instrument")
        )

    def trading_calendar(self, start_date: str, end_date: str) -> list[str]:
        """Return official exchange sessions from the receipt-backed calendar dataset."""
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        frame = self._load(
            "trading_calendar", start=start_date, end=end_date,
        )
        required = {"trade_date", "is_trading"}
        missing = required.difference(frame.columns)
        if missing:
            raise CNEquityDataError(
                f"trading_calendar missing columns: {sorted(missing)}"
            )
        if frame.is_empty():
            raise CNEquityDataError(
                f"trading_calendar returned no rows for {start_date}..{end_date}"
            )
        try:
            frame = frame.with_columns(
                pl.col("trade_date").cast(pl.String, strict=True),
                pl.col("is_trading").cast(pl.Boolean, strict=True),
            )
        except Exception as exc:
            raise CNEquityDataError(f"Invalid trading_calendar value types: {exc}") from exc
        if frame.select(
            pl.any_horizontal(pl.col("trade_date").is_null(), pl.col("is_trading").is_null()).any()
        ).item():
            raise CNEquityDataError("trading_calendar contains null date/session state")
        if frame.select(pl.col("trade_date").is_duplicated().any()).item():
            raise CNEquityDataError("trading_calendar contains duplicate trade_date keys")
        if frame.filter(
            (pl.col("trade_date") < start_date) | (pl.col("trade_date") > end_date)
        ).height:
            raise CNEquityDataError("trading_calendar returned dates outside requested window")
        sessions = (
            frame.filter(pl.col("is_trading"))
            .select("trade_date")
            .sort("trade_date")["trade_date"]
            .to_list()
        )
        if not sessions:
            raise CNEquityDataError(
                f"trading_calendar has no trading sessions for {start_date}..{end_date}"
            )
        return sessions

    def trading_status(
        self, instruments: list[str], start_date: str, end_date: str
    ) -> pl.DataFrame:
        """Return historical per-security trading status for execution checks."""
        frame = self._load(
            "trading_status", symbols=instruments, start=start_date, end=end_date,
        )
        required = {"symbol", "trade_date", "is_trading", "risk_warning", "status"}
        missing = required.difference(frame.columns)
        if missing:
            raise CNEquityDataError(f"trading_status missing columns: {sorted(missing)}")
        if frame.is_empty():
            raise CNEquityDataError("trading_status returned no evidence")
        if frame.select(pl.struct(["symbol", "trade_date"]).is_duplicated().any()).item():
            raise CNEquityDataError("trading_status contains duplicate symbol/date keys")
        if frame["risk_warning"].null_count():
            raise CNEquityDataError("trading_status risk_warning contains unknown/null states")
        return frame.rename({"symbol": "instrument", "trade_date": "date"}).sort(
            ["instrument", "date"]
        )

    def financial_statement_items(
        self, items: list[str], as_of_date: str, instruments: list[str] | None = None
    ) -> pl.DataFrame:
        """Read only strict PIT financial facts knowable by the requested date."""
        if not items:
            raise ValueError("At least one documented CNEquity item code is required")
        frame = self._load(
            "financial_statement_items", items=items, symbols=instruments,
            as_of=as_of_date, pit_mode="strict",
        )
        if frame.is_empty():
            raise CNEquityDataError(
                f"Strict PIT returned no rows as of {as_of_date}; this is a coverage failure"
            )
        missing = PIT_EVIDENCE_COLUMNS.difference(frame.columns)
        if missing:
            raise CNEquityDataError(
                f"Strict PIT evidence columns missing: {sorted(missing)}"
            )
        if frame["pit_is_exact"].null_count() or not frame["pit_is_exact"].all():
            raise CNEquityDataError("Strict PIT query returned missing/non-exact evidence")
        if frame["pit_quality"].null_count() or frame.filter(
            pl.col("pit_quality") != "strict"
        ).height:
            raise CNEquityDataError("Strict PIT query returned reconstructed/unknown quality")
        if "announce_date" not in frame.columns:
            raise CNEquityDataError("Strict PIT result missing announce_date")
        if frame.filter(pl.col("announce_date").cast(pl.String) > as_of_date).height:
            raise CNEquityDataError("PIT result contains facts announced after as_of")
        return frame
