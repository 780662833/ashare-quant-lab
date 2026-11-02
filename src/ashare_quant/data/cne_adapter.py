"""CNEquity 0.21.x adapter with fail-closed research evidence checks.

Raw OHLC remains the execution-price source. HFQ columns are research-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    def __post_init__(self) -> None:
        if self.config_path is not None and self.data_root is not None:
            raise ValueError("Pass config_path or data_root, not both")
        if self.config_path is None and self.data_root is None:
            raise ValueError("CNEquity config_path or data_root is required")
        self._config = None
        if self.config_path is not None:
            from cnequity.config import load_config
            self._config = load_config(Path(self.config_path).expanduser())

    def _load(self, dataset: str, **kwargs: Any) -> pl.DataFrame:
        from cnequity.query import load
        if self._config is not None:
            kwargs["config"] = self._config
        else:
            kwargs["data_root"] = str(Path(self.data_root).expanduser())
        return load(dataset, **kwargs)

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
