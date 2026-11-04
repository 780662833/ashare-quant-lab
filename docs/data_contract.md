# Data contract

CNEquity 0.21.0 is the sole authoritative provider of market and point-in-time
facts. The v1 default universe is the documented research-eligible
`cn_a_sh_sz_research_v1` profile (Shanghai/Shenzhen only); Beijing remains
excluded because CNEquity labels its all-exchange profile experimental.

`daily_bars` is historical price data, not itself a point-in-time universe.
Use the versioned profile for the selection-date universe, and read
`trading_status` separately for execution/no-fill decisions. Keep raw OHLC for
fills and use `adj_*` only for return/feature calculations.

For fundamentals, use `financial_statement_items` with both `as_of` and
`pit_mode="strict"`. CNEquity's `pit_quality` value for exact,
contemporaneously observed evidence is `strict`; `reconstructed`,
`snapshot_only`, `not_applicable`, null or missing evidence is not eligible
for model features. Empty strict results are a coverage failure, not permission
to fall back to best-effort.

Application code accesses CNEquity through the adapter and the explicit
acceptance runner; strategy/model modules must not query CNEquity internals.

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

## Four-gate data admission

No model-training input may be built until all four gates pass for the exact
research window, universe profile, and CNEquity data revisions:

1. **Historical universe** — historical membership, interior trading-session
   gaps, ST evidence, listing/delisting and survivorship checks for the
   selected versioned profile.
2. **HFQ adjustment** — require `adjust="hfq"` and `strict_adj=True`, then
   run revision-bound factor diagnostics. Factor coverage alone is not
   historical adjustment accuracy.
3. **PIT fundamentals** — query only with `as_of` and
   `pit_mode="strict"`; require non-empty rows, `pit_is_exact=True`,
   `pit_quality="strict"`, complete requested symbol/item coverage, and
   availability no later than the simulated decision time.
4. **Trading status** — the current acceptance check validates evidence and
   risk-warning state for observed positive-volume SH/SZ stock bars and
   surfaces contradictions.

Any missing evidence is FAIL. There is no best-effort fallback. The
acceptance report records the requested window, profile, counts, blockers,
software versions and CNEquity read receipts/revisions.

**Scope limitation:** the current trading-status check is based on observed
positive-volume SH/SZ bars. It is not yet a complete instrument-by-calendar
matrix proving every suspension/non-trading date. That remains a Phase 5+
data-contract gap.

This report does not itself unlock training. A future training-data builder
must explicitly verify an overall PASS report for the same window/profile/data
revisions and preserve the report/receipts with the dataset.
`training_dataset_unlocked` remains false until that separate implementation.

A pure price-momentum benchmark does not consume fundamental PIT features, but
still requires valid historical membership, adjusted-price evidence, official
calendar, and execution-state evidence. This exception does not authorize model
training.

### Run locally on Windows

Use the project's current local lake path:

```powershell
python -m pip install -e ".[dev]"
python scripts/validate_cnequity_lake.py `
  --data-root "F:\\python\\ashare-quant-lab\\data\\cnequity" `
  --start-date 2018-01-01 `
  --end-date 2025-12-31
```

The CNEquity user configuration is resolved by its local config resolver;
avoid hard-coding an account-specific configuration path. Default PIT item
codes are CNEquity-documented `roe` and `net_profit`. Reports are written to
`reports/data_acceptance/` as JSON and Markdown. Exit code 0 means all gates
passed; exit code 2 means one or more gates failed. A successful report still
does not create a training dataset.

## Validation

Reject duplicate instrument/date keys, invalid OHLC, negative volume/amount,
missing required timestamps, and future-dated features. Represent missing data
explicitly; do not silently forward-fill unexplained gaps.

## Adapter boundary

The concrete adapter is pinned to CNEquity 0.21.0. Its query arguments,
profile semantics, PIT quality values and diagnostic APIs are taken from that
release's public API contract; a version upgrade requires a fresh contract
review and adapter tests.
