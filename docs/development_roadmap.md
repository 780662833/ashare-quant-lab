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
| 5 | Architecture, canonical research bundle and benchmark orchestration | Merged in PR #10, #35 and #36 | Receipt-backed CNEquity trading-calendar read and explicit raw/HFQ + weekly PIT universe + trading-status assembler are covered by synthetic tests; external audited historical price-limit evidence and local-lake acceptance remain pending |
| 6 | Chronological walk-forward fold planner | Merged in PR #11 | Expanding/rolling folds, label-maturity purge, embargo and non-overlapping test windows; no model fitting |
| 7 | Itemized execution-cost attribution | Merged in PR #12 | Separate commission, transfer fee, sell stamp duty and slippage; slippage is not double-deducted |
| 8 | Walk-forward runner, artifact persistence, verification and comparison | Merged in PR #13, #15, #16, #29, #30, #31 and #32 | Cutoff-isolated momentum folds; price-limit audit bound to assembled evidence rows and rule revision; experiment persistence/verification bound to structured adapter receipts; builder-only bundle admission at benchmark and walk-forward entry points; no model fitting |
| 9.1 | Cross-sectional feature and forward-label contracts | Merged in PR #58 | Weekly as-of features: 5/20/60/120-session HFQ returns, rolling volatility, raw amount/volume activity and strict PIT ROE/net-profit; exact 5-session forward-return labels carry label_end_date. Synthetic tests cover future-publication isolation, missing-session nulls and signal-date membership. |
| 9.2 | Training dataset admission and PIT source binding | Implemented in PR #59 and extended in PR under review | Fail-closed exact-window/profile acceptance, replayable universe/HFQ/PIT receipts, pinned financial_statement_items adapter revision, conservative announcement-date availability, numeric features and label maturity before cutoff. The acceptance report remains training_dataset_unlocked=false. |
| 9.3 | LightGBM adapter and weekly model target strategy | Merged in PR #60 | Deterministic LightGBM fit/predict/save/load requires a matching admission decision and exact feature/label fingerprints. Weekly predictions become Top-20 equal-weight target weights through the shared engine. No real model fit has been run. |
| 9.4 | Walk-forward model runner and matched momentum comparison | Merged in PR #61 | Per fold: train-only admission/fit, validation prediction, label-maturity re-admission/refit on train+validation, test-only predictions. Model and momentum reset to the same baseline NAV and use identical costs/execution settings. CI uses fake LightGBM and synthetic bundles; no real training or empirical run. |
| 9.5 | Model experiment persistence and verification | Merged in PR #62 | Persist per-fold Booster, feature/label predictions, targets, benchmark and model ledgers, admissions, provenance and config; verify SHA-256 inventory and model metadata. No real model artifacts generated. |
| 10.1 | Research QA architecture and acceptance criteria | Merged in PR #17 | Architecture, four-gate policy, regime/cost/fill matrices, parameter/OOS discipline and report contract documented |
| 10.2 | Admission guard and report skeleton | Merged in PR #18 and #34 | Preserve all four independent gate statuses; QA admission consumes structured source identities and actual adapter receipts through the shared fail-closed provenance validator; CI passed on PR #34 |
| 10.3 | Historical-period and market-regime diagnostics | Merged in PR #19 and #33 | Verified OOS fold/year summaries; trailing-only 63-session cross-sectional HFQ regime labels; builder-validated bundle admission and exact match of receipts, audits, rule identity, scope and gates to the experiment manifest |
| 10.4 | Transaction-cost stress | Merged in PR #20 | C0 exact persisted baseline plus 1.5x/2x/3x fee-and-slippage variants; immutable verified runs; aligned provenance/folds; cost deltas vs C0; empirical execution remains blocked without a verified C0 artifact |
| 10.5–10.7 | Execution, parameter/statistical QA and integrated acceptance | P10.5 adapter/assembler and fill-stress capability assessment merged in PR #21–23, #35–37; deterministic F0–F4 fill-stress suite merged in PR #44; P10.6 descriptive statistics merged in PR #38; P10.7 integrated report/writer merged in PR #39 | Receipt-backed runtime/adapter path and fail-closed canonical assembler exist. The shared engine supports deterministic target-quantity fill caps; PR #44 runs F0–F4 as immutable, verified walk-forward experiments aligned on provenance/config/folds. These caps are execution sensitivity controls, not a market-depth or volume-based liquidity model. Statistical summaries remain descriptive only. External historical price-limit provider, local CNEquity 0.23.0/lake verification and empirical C0 remain outstanding |
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

Completed momentum runs can be persisted through
`persist_walk_forward_momentum_run(...)`, then checked with
`verify_walk_forward_experiment(...)`. Parameter variants can be compared with
`compare_walk_forward_experiments(...)` only when acceptance identity, source
revisions, universe, fold dates and backtest/cost configuration are identical.
The comparison reports results; it does not select or tune against OOS test
windows. No data assembly, scheduler or model-training unlock is added. See
[experiment_store.md](experiment_store.md).

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
- Phase 9 admission verifies the exact acceptance window/profile, all five
  report evidence entries (historical universe, replayable universe receipt,
  HFQ, strict PIT and trading status), replayable source revisions, feature
  schema/keys and label maturity before the fold cutoff.
- The acceptance report and bundle provenance retain
  `training_dataset_unlocked=false`; the model runner consumes a successful
  per-fold admission object before each explicit LightGBM fit.

### Phase 9.6 — optional Qlib DatasetH bridge (merged in PR #64; CI test correction in PR #65)

The bridge converts only an admitted Polars training panel into Qlib's
`DataHandlerLP.from_df` and `DatasetH` contracts. It rechecks feature/label
fingerprints, exact key alignment, finite values, label maturity and
non-overlapping segments. Qlib/pandas imports are lazy; base CI stubs the
optional API. PIT/source receipts, admission, walk-forward boundaries and
A-share execution remain project-owned. Synthetic CI validates conversion
only; no real Qlib training or empirical strategy result is implied.

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
