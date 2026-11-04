# Canonical research data bundle (Phase 5)

## Purpose

`ResearchDataBundle` is the stable boundary between provider-specific data
loading and strategy/backtest code. It normalizes already-read inputs; it does
not make CNEquity calls, download data, create features/labels, or unlock model
training.

The first orchestration path is the price-only momentum benchmark.

## Builder contract

Call `build_research_data_bundle(...)` with:

| Input | Required fields | Purpose |
|---|---|---|
| `raw_bars` | `instrument,date,open,high,low,close,volume,amount` | Raw execution/marking prices |
| `adjusted_bars` | `instrument,date,adj_close,adj_is_exact` | Exact HFQ signal prices |
| `universe_membership` | `instrument,date` | Historical as-of membership snapshots |
| `execution_evidence` | `instrument,date,is_suspended,is_limit_up_locked,is_limit_down_locked,limit_up,limit_down,risk_warning_known,limit_evidence_source,limit_rule_version` | Joined native status and separate versioned price-limit evidence |
| `trading_dates` | ordered exchange-local session dates | Session and next-open timing |
| `acceptance_report` | matching window/profile and named gate results | Evidence admission |
| `source_revisions` | `daily_bars`, `adj_factors`, `universe`, `trading_status`, `trading_calendar`, `price_limit_rules` | Reproducibility |

The acceptance report must show PASS for historical universe, HFQ adjustment,
and trading status, and include replayable receipts for historical universe
and HFQ. Any failed historical-universe receipt sub-gate is a hard failure. The pure momentum benchmark does not consume fundamental
PIT fields. A failed PIT gate therefore does not block this **price-only
benchmark**; it still blocks all model-training work. The builder never sets
`training_dataset_unlocked` to true.

## Fail-closed validation

- All keyed frames have unique, non-null `(instrument,date)` keys within the
  exact report window.
- Raw OHLCV is finite and internally valid.
- Every raw bar has a matching explicit execution-evidence row and known
  risk-warning state; a positive-volume row marked non-trading is rejected.
- Price-limit evidence carries a non-empty source, rule version and per-row
  regime code (including explicit no-limit regimes); these are retained in
  bundle provenance. The resolver must be independently
  validated for board/date-specific limits and IPO no-limit periods.
- Limit prices are either null (not applicable) or finite and positive.
- Every raw bar has exactly one matching adjusted row with
  `adj_is_exact=True`; adjusted and raw key scopes must match.
- Calendar sessions are unique and match the raw-bar date coverage.
- Point-in-time membership must include every weekly signal date; unknown
  membership symbols are rejected.
- Acceptance report window/profile, replayable receipts and source revisions
  are retained as provenance. A different window or profile is rejected.
- Source revisions must explicitly cover daily bars, factors, historical
  universe, trading status, exchange calendar, and the separate limit-rule source.

No status flag, adjusted price, or universe row is inferred or forward-filled.

## Output separation

- `bundle.execution_bars` contains raw OHLCV and the engine's explicit
  execution-state columns. It contains no adjusted prices.
- `bundle.adjusted_bars` contains HFQ close and exactness. It contains no raw
  execution OHLC.
- `bundle.universe_membership` contains signal-date membership keys.
- `bundle.trading_dates` is the normalized exchange calendar.
- `bundle.provenance` retains acceptance report identity, gate results,
  source receipts/revisions, and requested scope.

## End-to-end benchmark

`run_momentum_benchmark(bundle, ...)` executes the full deterministic path:

1. Compute 126/21 momentum scores (or explicit test overrides).
2. Convert scores into deterministic Top-N equal-weight targets.
3. Send targets and raw execution bars to the shared backtest engine.
4. Produce the common performance/execution report.
5. Return signals, targets, daily NAV, orders, holdings, report, and provenance.

This is a reproducible synthetic integration path. It does not certify that
the current local CNEquity lake can yet provide complete normalized inputs.
CNEquity 0.21.0's native `trading_status` maps only the trading state and
nullable ST/risk-warning state. Its stable `daily_bars` and
`trading_status` contracts do not contain price-limit values or locked-limit
flags. `normalize_cnequity_trading_status()` maps the native status facts;
`combine_execution_evidence()` joins a separately supplied, versioned
price-limit evidence table. The project must not derive limit values or lock
states by guessing a percentage from the previous close or current board.
Until a verified rule/evidence provider supplies those fields, the bundle
builder correctly refuses to run the historical execution backtest.
