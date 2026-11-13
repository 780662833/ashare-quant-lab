# Canonical research data bundle (Phase 5)

## Purpose

`ResearchDataBundle` is the stable boundary between provider-specific data
loading and strategy/backtest code. It normalizes already-read inputs; it does
not make CNEquity calls, download data, create features/labels, or unlock model
training.

Only `build_research_data_bundle(...)` creates an admitted bundle. Direct dataclass
construction is rejected by benchmark and walk-forward entry points. Internal
walk-forward cutoff views preserve the builder's validation marker without
reopening provider access or changing the data-admission decision.

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
| `source_revisions` | Structured revision ID + contract fingerprint identities; external limit rules also include provider source | Reproducibility, checked against receipts |
| `source_receipts` | Actual `CNEquityAdapter.source_receipts` records | Committed dependency revisions and contracts |
| `coverage_audits` | Dataset-keyed explicit completeness audit records | Prove expected/observed key coverage for each required source |

The acceptance report must show PASS for historical universe, the explicit
historical-universe-receipt sub-gate, HFQ adjustment, and trading status, and
include replayable receipts for historical universe and HFQ. The receipt
sub-gate must be present (absence is not treated as PASS); any failed
historical-universe receipt sub-gate is a hard failure. The pure momentum benchmark does not consume fundamental
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

## CNEquity read-session provenance

`CNEquityAdapter` keeps a per-instance read session. The first
receipt for a dependency pins its `dependencies[dataset].revision_id`
and `contract_fingerprint`; later reads pass the accumulated
per-dataset map through `revision_map` and reject any revision or
contract drift for dependencies already seen. The adapter exposes the observed
`source_revision_map` and `source_contract_fingerprints`
alongside the ordered source receipts.

This is a **dependency-consistent read session**, not a lake-wide atomic
snapshot. CNEquity receipts cover only the dependencies of each individual
query. A dependency first encountered by a later query is pinned at that
query's read time; no atomic cross-dataset instant is claimed. The accumulated
map and every receipt must be retained in the experiment manifest. The
`coverage.completeness` field remains `not_assessed`
unless a separate dataset-specific coverage audit proves completeness.

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


## Receipt-bound source identity and completeness audit (Phase 10.5)

The builder no longer accepts free-form source revision strings. Callers must supply the actual adapter source_receipts records. The builder aggregates each receipt's dependencies identities and checks that declared source_revisions match committed revision_id and contract_fingerprint values for daily_bars, adj_factors, trading_status, and trading_calendar. The historical-universe identity is an explicit alias of the receipt-bound daily_bars identity. External price_limit_rules remain separately versioned with a provider identity; they are not represented as CNEquity dependencies. The declared `price_limit_rules.revision_id` must also exactly match every assembled execution-evidence row's `limit_rule_version`; matching the provider source label alone is insufficient.

Every bundle requires dataset-specific coverage_audits for daily bars, HFQ factors, historical universe, trading status, trading calendar, and price-limit rules. Each audit must match the requested window, identify its audit, method, and source, reconcile expected and observed row counts, and report zero missing or unexpected keys. For datasets assembled inside the builder, the audit observed count must equal the assembled row/session count.

A CNEquity receipt with coverage.completeness=not_assessed remains unproven metadata; it is never promoted to complete merely because rows exist or min/max dates span the requested window. The separate audit is required for admission. The builder validates audit structure and agreement with assembled data but does not itself create or independently certify the upstream audit. Synthetic tests use explicitly synthetic audits and do not constitute real-lake completeness evidence.
