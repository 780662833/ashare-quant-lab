# Development roadmap

**Baseline date:** 2026-11-04  
**Product:** A-share quantitative research MVP v1

This roadmap separates deterministic platform implementation from background
CNEquity data acquisition. Data-download completeness is not the primary
progress metric; a runnable, testable, bias-aware research loop is.

## Frozen MVP acceptance criteria

A useful v1 experiment must test:

1. Genuine out-of-sample stock-ranking ability.
2. Excess return after configured costs and unfilled-order constraints.
3. Stability across chronological market regimes, not one favorable period.

Scope remains one weekly cross-sectional strategy plus one weekly momentum
benchmark, top 20 equal-weight, no leverage. Live trading, ETFs, LLM decisions,
advanced optimization, multi-strategy orchestration, and a complex dashboard
remain out of scope.

## Delivery phases

| Phase | Deliverable | Status | Exit criteria |
|---|---|---|---|
| 0. Foundation | Python package, frozen v1 scope, initial contracts/tests | Merged | Reproducible skeleton and documented scope |
| 1. CNEquity integration | Pinned 0.21.0 adapter and canonical source boundary | Merged | Provider calls isolated; raw/adjusted prices separated |
| 1.5–1.6. Data acceptance | Historical universe, HFQ, strict PIT, trading-status checks | Merged | Fail-closed evidence report; does not unlock training |
| 2. Backtest core | Cash/position/NAV accounting, next-open orders, costs, no-fill | Merged | Synthetic tests pass; exchange gaps documented |
| 3. Report | Performance, risk, turnover, fill, costs, benchmark comparison | Merged | Synthetic metric tests pass and definitions documented |
| 4. Momentum benchmark | Weekly 126/21 momentum, PIT membership, top-20 equal-weight | Merged | Synthetic no-lookahead and shared-engine tests pass |
| 5. Research data bundle | Canonical price/universe/calendar/execution evidence inputs and momentum orchestration | Core implemented on PR #10; CNEquity mapping/local-lake validation pending | Deterministic builder validates keys, coverage, dates, receipts and provenance; no training unlock |
| 6. Walk-forward framework | Chronological folds and common benchmark/model evaluation windows | Planned | No random splits; fold and timestamp boundaries tested |
| 7. Feature/label + model adapter | PIT features, future-only labels, Qlib/LightGBM | Planned; gated | Four gates pass for same revisions/window; builder checks exact acceptance identity |
| 8. Robustness and research QA | Cost, fill, universe, period and regime sensitivity | Planned | Reproducible net-of-cost model/benchmark comparison |
| 9. Optional polish | Reports and small inspection UI | Deferred | Only after research validity is demonstrated |

## Phase 5: detailed scope

Build a canonical `ResearchDataBundle` boundary connecting the CNEquity
adapter to strategy and backtest modules without making strategies
provider-aware.

### Inputs

- Official exchange trading calendar for the requested window.
- Raw daily OHLC/volume/amount for execution and marking.
- Exact HFQ adjusted close for momentum features.
- Point-in-time eligible universe membership on every weekly signal date.
- Historical trading status and risk-warning evidence.
- Historical limit prices and limit-lock evidence required by the engine.
- Source receipts/revisions and applicable acceptance-report identity.

### Outputs

- `adjusted_bars`: `instrument,date,adj_close` and exactness/provenance.
- `universe_membership`: `instrument,date` with point-in-time semantics.
- `execution_bars`: raw OHLC plus `is_suspended`,
  `is_limit_up_locked`, `is_limit_down_locked`, `limit_up`, and
  `limit_down`.
- `trading_dates`: ordered official exchange sessions.
- `provenance`: requested window, profile, source revisions/receipts,
  coverage gaps, and relevant acceptance-report identity.

### Non-goals

- Do not download/backfill the complete historical lake as part of Phase 5.
- Do not train a model or create labels/features.
- Do not derive unknown limit-lock state from an assumed rule.
- Do not convert missing bars/status into tradable rows.
- Do not silently fill adjusted prices or historical membership.
- Do not claim synthetic CI means real-lake acceptance passed.

### Exit criteria

- Synthetic assembly tests cover duplicates, missing dates/status, invalid
  prices, incorrect joins, and PIT membership.
- Strategy code consumes canonical bundle fields and does not import CNEquity.
- Engine receives raw execution bars; momentum receives adjusted bars and
  signal-date membership.
- Bundle records scope/provenance and fails closed on insufficient evidence.
- `training_dataset_unlocked` remains false.

## Data acquisition policy

CNEquity downloads may continue at low priority/background pace. The current
Windows project data root is:

`F:\\python\\ashare-quant-lab\\data\\cnequity`

Use the CNEquity config resolver rather than baking a user-specific absolute
configuration path into project code.

When a source batch is degraded or writes zero rows, do not assume the gap was
filled. Inspect coverage/publication/fallback and failed-batch reason fields
before retrying. Run status alone is not evidence that the requested historical
range became usable.

## Phase completion rules

A phase is complete only when code/tests are committed, CI passes on the
current PR head, documentation describes actual behavior and limitations,
and no unsupported claim is made about local data coverage or research
validity. Downstream work must distinguish code present on a PR branch from
code merged into `main`.
