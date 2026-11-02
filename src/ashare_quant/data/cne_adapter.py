"""CNEquity 0.21.x adapter.

CNEquity remains the source of truth. Execution prices stay unadjusted;
adjusted columns are research features only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_PROFILE = "cn_a_sh_sz_research_v1"


class CNEquityDataError(RuntimeError):
    """Raised when CNEquity data cannot safely enter a research run."""


@dataclass(slots=True)
class CNEquityAdapter:
    """Read market data and PIT universe facts from a CNEquity lake."""

    config_path: str | Path | None = None
    data_root: str | Path | None = None
    profile: str = RESEARCH_PROFILE

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
        return frame.rename({"symbol": "instrument", "trade_date": "date"}).sort(
            ["instrument", "date"]
        )

    def daily_bars(
        self, instruments: list[str] | None, start_date: str, end_date: str
    ) -> pl.DataFrame:
        """Return raw execution OHLC plus exact HFQ research columns."""
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        frame = self._load(
            "daily_bars", start=start_date, end=end_date, symbols=instruments,
            adjust="hfq", strict_adj=True,
        )
        if frame.is_empty():
            raise CNEquityDataError(f"No daily_bars for {start_date}..{end_date}")
        if "adj_is_exact" not in frame.columns or not frame["adj_is_exact"].all():
            raise CNEquityDataError("HFQ factors are not exact for every returned bar")
        return self._canonical_bars(frame)

    def point_in_time_universe(self, as_of_date: str) -> pl.DataFrame:
        """Return strict research-eligible SH/SZ securities on one date."""
        frame = self._load(
            "daily_bars", start=as_of_date, end=as_of_date, profile=self.profile,
        )
        if frame.is_empty():
            raise CNEquityDataError(
                f"No eligible securities on {as_of_date}; check lake coverage/evidence"
            )
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
        return frame.rename({"symbol": "instrument", "trade_date": "date"}).sort(
            ["instrument", "date"]
        )

    def financial_statement_items(
        self, items: list[str], as_of_date: str, instruments: list[str] | None = None
    ) -> pl.DataFrame:
        """Read only strict PIT financial facts knowable by the requested date."""
        frame = self._load(
            "financial_statement_items", items=items, symbols=instruments,
            as_of=as_of_date, pit_mode="strict",
        )
        if frame.is_empty():
            return frame
        if "pit_is_exact" in frame.columns and not frame["pit_is_exact"].all():
            raise CNEquityDataError("Strict PIT query returned non-exact evidence")
        if "pit_quality" in frame.columns:
            bad = frame.filter(~pl.col("pit_quality").is_in(["strict", "exact"]))
            if not bad.is_empty():
                raise CNEquityDataError("Strict PIT query returned reconstructed/unknown rows")
        return frame
