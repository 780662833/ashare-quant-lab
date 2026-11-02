# Data contract

CNEquity 0.21.0 is the sole authoritative provider of market and point-in-time facts. The v1 default universe is the official research-eligible `cn_a_sh_sz_research_v1` profile (Shanghai/Shenzhen only); Beijing remains excluded because CNEquity labels its all-exchange profile experimental.

`daily_bars` is historical price data, not itself a point-in-time universe. Use the versioned profile for the selection-date universe, and read `trading_status` separately for execution/no-fill decisions. Keep raw OHLC for fills and use `adj_*` only for return/feature calculations. Strict HFQ coverage is required before using adjusted values.

For fundamentals, use `financial_statement_items` with both `as_of` and `pit_mode="strict"`. CNEquity's current contract identifies this dataset as partial/reconstructed at the dataset level; strict mode excludes reconstructed backfills and only rows with exact evidence may enter model features. Empty strict results are a data-coverage failure, not permission to fall back to best-effort.
Application code accesses it only through `data/cne_adapter.py`; strategy and
model modules must not query CNEquity internals directly.

## Canonical keys and timestamps
- Security key: stable exchange-qualified symbol (e.g. `600000.SH`).
- Market date: exchange-local trading date.
- Decision timestamp: Asia/Shanghai timestamp when all inputs are known.
- Availability timestamp: earliest time a source fact was knowable.
- Every panel row is uniquely keyed by (instrument, trading_date).

## Required market fields
Open, high, low, close, volume, amount, adjustment/corporate-action basis,
suspension status, price-limit status/price, and source provenance.

## Universe and PIT fundamentals
Historical listing/delisting state, ST status history, exchange/board, and
tradability state must be point-in-time. Store report period separately from
publication/availability timestamp. A report-period date is not publication
time; revisions must not overwrite historically knowable values.

## Validation
Reject duplicate instrument/date keys, invalid OHLC, negative volume/amount,
missing required timestamps, and future-dated features. Represent missing data
explicitly; do not silently forward-fill unexplained gaps.

## Adapter boundary
Finalize the concrete adapter only after inspecting the pinned CNEquity revision
and actual query APIs. Do not guess method names or schemas.
