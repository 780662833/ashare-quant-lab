"""Thin CNEquity boundary.

Concrete implementation is deferred until the exact CNEquity revision and
public query API have been inspected. Do not add guessed imports or methods.
"""

from typing import Protocol

import polars as pl


class MarketDataAdapter(Protocol):
    """Minimal market-data contract consumed by research code."""

    def daily_bars(self, instruments: list[str], start_date: str, end_date: str) -> pl.DataFrame:
        """Return canonical daily bars for the requested interval."""
        ...

    def point_in_time_universe(self, as_of_date: str) -> pl.DataFrame:
        """Return securities eligible in the universe as of a historical date."""
        ...
