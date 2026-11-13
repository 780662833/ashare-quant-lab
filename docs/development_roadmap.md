# Development roadmap

**Baseline:** 2026-11-13  
**Project:** A-share quantitative research MVP v1

This roadmap tracks the research platform, not the volume of downloaded CNEquity
history. Historical bulk backfill is paused by user decision; existing gaps stay
recorded and must never be disguised as accepted research input. Recent IPOs are
not missing before their listing dates.

## Frozen v1 objective and scope

The system must determine whether a cross-sectional stock-selection strategy
has genuine out-of-sample ranking ability, whether excess return survives
configured costs and no-fill constraints, and whether performance persists
across chronological market regimes.

Frozen scope: one weekly cross-sectional model strategy, one weekly momentum
benchmark, top 20 equal weight, no leverage, shared A-share backtest engine,
daily NAV/positions/orders, performance/risk reporting and walk-forward
evaluation. Live trading, LLM decisions, ETFs, complex optimizers,
multi-strategy orchestration, and a complex dashboard are excluded.

## Phase and merge status

The PR states below are repository states, not implied by CI results. A PR
branch is not part of `main` until GitHub reports it merged.

| Phase | Deliverable | Repository state | Acceptance evidence / remaining boundary |
|---|---|---|---|
| 0 | Project foundation and frozen scope | Merged | Package, base contracts, tests and documentation |
| 1 | CNEquity 0.21.0 adapter | Merged | Provider access isolated behind adapter; raw and adjusted prices distinct |
| 1.5–1.6 | Four-gate local data acceptance | Merged | Fail-closed JSON/Markdown acceptance report; no automatic training unlock |
| 2 | Deterministic backtest core | Merged | Synthetic execution/accounting tests; not a full historical exchange simulator |
| 3 | Performance and benchmark report | Merged | Performance, risk, fill, turnover, cost and benchmark metrics |
| 4 | Weekly momentum benchmark | Merged | 126/21 HFQ momentum, PIT membership, Top-N equal weight and shared-engine tests |
| 5 | Architecture, canonical research bundle and benchmark orchestration | Merged in PR #10 | Canonical bundle, native status normalization and momentum orchestration have synthetic coverage; direct CNEquity assembly, audited historical price-limit evidence and local-lake acceptance remain pending |
| 6 | Chronological walk-forward fold planner | Merged in PR #11 | Expanding/rolling folds, label-maturity purge, embargo and non-overlapping test windows; no model fitting |
| 7 | Itemized execution-cost attribution | Merged in PR #12 | Separate commission, transfer fee, sell stamp duty and slippage; slippage is not double-deducted |
| 8 | Walk-forward experiment runner | Merged in PR #13; GitHub Actions run #60 passed on its PR head | Executes the deterministic momentum benchmark fold by fold, with cutoff-isolated inputs and test-only ledgers; model runner remains later work |
| 9 | Point-in-time feature/label and model adapter | Planned; four-gate blocked | Only after acceptance proof for identical dates/profile/data revisions |
| 10 | Robustness and research QA | Planned | Costs/fill/universe/period/regime sensitivities and reproducible comparisons |
| 11 | Optional report inspection UI | Deferred | Only after research validity is demonstrated |

## Phase 5 — canonical research data bundle

Create one provider-neutral boundary between source data and strategies.

Inputs:
- Official exchange trading calendar.
- Raw OHLCV for execution and marking.
- Exact HFQ adjusted close for signal calculation.
- Point-in-time membership on each signal date.
- Native trading status and risk-warning evidence.
- Separate historical limit prices, lock flags and per-row limit-regime evidence.
- Source revision identifiers and replayable acceptance receipts.

Outputs:
- `adjusted_bars`: HFQ signal prices, never execution prices.
- `execution_bars`: raw OHLCV and complete execution-state evidence.
- `universe_membership`: dated historical membership, never current constituents backfilled.
- `trading_dates`: explicit official exchange sessions.
- `provenance`: data window, profile, receipts, revisions and coverage.

CNEquity 0.21.0 does not publish daily limit prices or locked-limit flags in its
stable `daily_bars` / `trading_status` contracts. A separate, audited and
versioned limit-rule evidence source is therefore required. No board percentage,
IPO no-limit period or lock state may be guessed.

The price-only momentum benchmark requires historical-universe, HFQ and
trading-status evidence for its exact window/revisions. It does not consume
fundamental PIT values, but that exception does not permit model training.
The four gates remain mandatory for any model-training dataset.

## Phase 6 — walk-forward fold planner

Use an explicit exchange calendar, never random splits. Each fold records:
- Train start/end and label-maturity date.
- Validation start/end and label-maturity date.
- Test start/end.
- Expanding or fixed-length rolling training mode.
- Label horizon and additional embargo sessions.

Training labels must mature before validation begins; validation labels must
mature before test begins. Test windows cannot overlap. The fold planner only
plans dates; it does not create labels, fit models, tune models, or backtest.

## Phase 7 — cost attribution

The order ledger records separately:
- Commission (including minimum commission).
- Transfer fee.
- Sell stamp duty.
- Slippage cost versus the raw open.

The first three sum to `fees`. Slippage is already embedded in the worse
fill price and must not be deducted from cash twice. Reports show both fees
and total attributed transaction costs. Rates are experiment assumptions and
must be saved with each run, not represented as current legal/broker schedules.

## Phase 8 — walk-forward experiment runner

The deterministic momentum fold runner is implemented in PR #13. It binds
each chronological fold to a cutoff-isolated bundle prefix, replays the fixed
momentum rule, and emits test-only daily NAV, orders, holdings and fold-level
metrics. A follow-up regression fix preserves actual first-test-session NAV
values while measuring cumulative return and drawdown from the prior-session
close. This phase does not fit a model; any future model runner must use the
same fold definitions and remain behind the four-gate training admission.

## Phase 9 — features, labels and model adapter (gated)

- Features must have availability timestamps no later than the decision time.
- Labels must use strictly future outcomes and remain separate from features.
- No random time-series splitting.
- No train or validation row may use a fact published after its simulated
  decision timestamp.
- Qlib may be reused for dataset/model workflow where its API fits; LightGBM
  provides the first model.
- The model uses the same target-weight interface, engine, cost settings and
  report as momentum.
- A builder must verify all four acceptance gates for the exact same window,
  profile and source revisions and retain the report/receipts with the dataset.
- `training_dataset_unlocked` remains false until that independent builder is
  implemented, tested and explicitly approves the exact dataset.

## CNEquity data acquisition policy

The user's current Windows lake root is:

`F:\cnEquity`

Use CNEquity's config resolver; do not hard-code the user-level config file.
Do not trigger repeated full downloads as part of ordinary research-platform
development. A degraded/zero-row backfill is not proof of coverage; inspect
coverage/publication/fallback and failed-batch reasons before retrying.

## Phase completion standard

A phase is complete only when code, tests and documentation are committed,
CI passes on the current PR head, the PR is merged before it is described as
part of `main`, and all limitations are stated honestly. Synthetic CI does
not certify real-lake coverage or empirical performance.
