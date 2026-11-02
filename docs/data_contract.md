# Data contract

CNEquity is the sole authoritative provider of market and point-in-time facts.
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
