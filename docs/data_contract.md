# Data contract

CNEquity 0.21.0 is the sole authoritative provider of market and point-in-time facts. The v1 default universe is the official research-eligible `cn_a_sh_sz_research_v1` profile (Shanghai/Shenzhen only); Beijing remains excluded because CNEquity labels its all-exchange profile experimental.

`daily_bars` is historical price data, not itself a point-in-time universe. Use the versioned profile for the selection-date universe, and read `trading_status` separately for execution/no-fill decisions. Keep raw OHLC for fills and use `adj_*` only for return/feature calculations.

For fundamentals, use `financial_statement_items` with both `as_of` and `pit_mode="strict"`. CNEquity's `pit_quality` value for exact, contemporaneously observed evidence is `strict`; `reconstructed`, `snapshot_only`, `not_applicable`, null or missing evidence is not eligible for model features. Empty strict results are a data-coverage failure, not permission to fall back to best-effort.

Application code accesses CNEquity through the adapter and the explicit acceptance runner; strategy/model modules must not query CNEquity internals directly.

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
research window and CNEquity data revisions:

1. **Historical universe** — CNEquity's `historical_universe_validity()`
   verifies window coverage, interior trading-session gaps, historical ST
   evidence receipts, and delisted-security survivorship for the selected
   versioned profile.
2. **HFQ adjustment** — require `adjust="hfq"` and `strict_adj=True`, then
   run CNEquity's revision-bound `publish_factor_diagnostics()`. Factor
   coverage alone is not accuracy. Missing/unassessed diagnostics or unresolved
   historical factor/action contradictions fail this gate.
3. **PIT fundamentals** — query only with `as_of` and
   `pit_mode="strict"`; require non-empty rows, `pit_is_exact=True`,
   `pit_quality="strict"`, and complete coverage for the requested
   symbol/item pairs.
4. **Trading status** — every positive-volume SH/SZ stock bar must have
   corresponding trading-status evidence, known risk-warning state, and no
   contradiction between a print and a non-trading/suspended status.

Any missing evidence is FAIL. There is no best-effort fallback. The acceptance
report records the requested window, profile, symbol/row counts, monthly
coverage, blockers, software versions and CNEquity read receipts/revisions.
This report does not itself unlock training; a future training-data builder
must explicitly verify an overall PASS report for the same window/profile and
must preserve the report/receipts with the dataset.

### Run locally on Windows

The local CNEquity lake is not available to GitHub Actions. From the repository
root, install the project dependencies and run:

```powershell
python -m pip install -e ".[dev]"
python scripts/validate_cnequity_lake.py `
  --data-root "F:\\cnEquity" `
  --start-date 2018-01-01 `
  --end-date 2025-12-31
```

The default PIT item codes are CNEquity-documented `roe` and `net_profit`.
They can be overridden with `--pit-items` and must be valid CNEquity item
codes. Reports are written to `reports/data_acceptance/` as JSON and Markdown.
Exit code 0 means all four gates passed; exit code 2 means one or more gates
failed. A successful report still does not create a training dataset.

## Validation

Reject duplicate instrument/date keys, invalid OHLC, negative volume/amount,
missing required timestamps, and future-dated features. Represent missing data
explicitly; do not silently forward-fill unexplained gaps.

## Adapter boundary

The concrete adapter is pinned to CNEquity 0.21.0. Its query arguments,
profile semantics, PIT quality values and diagnostic APIs are taken from that
release's public API contract; changes to the pinned upstream version require
a fresh contract review and adapter tests.
