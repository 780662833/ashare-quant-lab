# Development roadmap

**Baseline:** 2026-11-14  
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
| 9.2 | Training dataset admission and PIT source binding | Merged in PR #59 and #63 | Fail-closed exact-window/profile acceptance, replayable universe/HFQ/PIT receipts, pinned financial_statement_items adapter revision, conservative announcement-date availability, numeric features and label maturity before cutoff. The acceptance report remains training_dataset_unlocked=false. |
| 9.3 | LightGBM adapter and weekly model target strategy | Merged in PR #60 | Deterministic LightGBM fit/predict/save/load requires a matching admission decision and exact feature/label fingerprints. Weekly predictions become Top-20 equal-weight target weights through the shared engine. No real model fit has been run. |
| 9.4 | Walk-forward model runner and matched momentum comparison | Merged in PR #61 | Per fold: train-only admission/fit, validation prediction, label-maturity re-admission/refit on train+validation, test-only predictions. Model and momentum reset to the same baseline NAV and use identical costs/execution settings. CI uses fake LightGBM and synthetic bundles; no real training or empirical run. |
| 9.5 | Model experiment persistence and verification | Merged in PR #62 | Persist per-fold Booster, feature/label predictions, targets, benchmark and model ledgers, admissions, provenance and config; verify SHA-256 inventory and model metadata. No real model artifacts generated. |
| 9.6 | Optional Qlib DatasetH bridge | Merged in PR #64–#66 | Lazy Qlib bridge for admission-checked Polars features/labels, `(datetime, instrument)` index, feature/label groups and chronological segments. Synthetic-only conversion tests. |
| 9.7 | Qlib DatasetH → LightGBM → weekly Top-20 pipeline | Merged in PR #67 | Walk-forward runner builds admission-bound Qlib datasets, fits the single LightGBM regressor from Qlib learn segments, predicts feature-only validation/test segments, and returns keyed scores to existing Top-20 equal-weight targets. PR #67 main CI passed 286 synthetic tests. |
| 9.8 | Real Qlib 0.9.7 API compatibility | Merged in PR #68 | Python 3.12.16, Qlib 0.9.7, Pandas 2.3.3 and LightGBM 4.7.0; actual `DataHandlerLP.from_df`, `DatasetH.prepare`, learn/infer views, datetime MultiIndex and LightGBM Dataset construction passed one synthetic smoke test. `lightgbm.train` was stubbed; no model fit or real-data backtest. |
| 9.9 | Full synthetic walk-forward integration with real Qlib | Merged in PR #69 | Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0. Real multi-fold runner exercised admission, DatasetH learn/infer, LightGBM Dataset construction, validation/test predictions, Top-20 targets, and shared model+momentum backtest paths; 2 real-API tests passed. `lightgbm.train` was replaced by a prediction-only Booster; no optimizer fit, local data lake or empirical result. |
| 9.10 | Matured validation ranking diagnostics | Merged in PR #70 | Per-week validation Rank IC / Pearson IC / Top-N excess vs labeled universe / equal-sized Top-Bottom spread; labels must mature strictly before test start. Test labels are rejected, metrics are archived per fold and do not tune model parameters. Base CI: 294 passed, 2 skipped; real Qlib API integration: 2 passed. |
| 9.11 | Cross-fold validation stability and explicit acceptance policy | Merged in PR #71 | Summarizes weekly Rank IC distribution/ICIR, positive-week and positive-fold ratios, Top-N excess stability and pooled label coverage. Explicit policy requires minimum folds, dates, valid IC weeks, cross-section width, coverage and ranking thresholds; values are versioned/fingerprinted. No policy means NOT_ASSESSED; insufficient sample means INSUFFICIENT_EVIDENCE. Base CI: 305 passed, 2 skipped; real Qlib integration: 2 passed. |
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
- Qlib `DatasetH` is the primary research dataset interface and segment
  selector; LightGBM is the single cross-sectional weekly rebalance-cycle
  return regressor. Labels align to next-open entry and next-cycle next-open
  exit; the session count can shorten around exchange holidays. No second
  model family is introduced.
- The model uses the same target-weight interface, engine, cost settings and
  report as momentum.
- Phase 9 admission verifies the exact acceptance window/profile, all five
  report evidence entries (historical universe, replayable universe receipt,
  HFQ, strict PIT and trading status), replayable source revisions, feature
  schema/keys and label maturity before the fold cutoff.
- The acceptance report and bundle provenance retain
  `training_dataset_unlocked=false`; the model runner consumes a successful
  per-fold admission object before constructing a Qlib dataset or fitting.
- The project's fold planner owns label-entry lag, open-to-open label-horizon
  purge, and embargo. The reported maturity date includes the one-session
  close-to-next-open entry delay. Qlib receives those approved boundaries as
  `DatasetH` segments; it does not weaken or replace admission.
- Python 3.13 remains the main development environment. Qlib/pandas remain
  optional and lazy; a dedicated Python 3.12 Qlib 0.9.7 smoke workflow checks
  real DatasetH/LightGBM data-interface compatibility without changing the
  project's base interpreter.

### Phase 9.6 — optional Qlib DatasetH bridge (merged in PR #64; CI test correction in PR #65)

The bridge converts only an admitted Polars training panel into Qlib's
`DataHandlerLP.from_df` and `DatasetH` contracts. It rechecks feature/label
fingerprints, exact key alignment, finite values, label maturity and
non-overlapping segments. Qlib/pandas imports are lazy; base CI stubs the
optional API. PIT/source receipts, admission, walk-forward boundaries and
A-share execution remain project-owned. Synthetic CI validates conversion
only; no real Qlib training or empirical strategy result is implied.

### Phase 9.7 — integrated Qlib DatasetH → LightGBM pipeline (merged in PR #67)

The diagnostic and final walk-forward fits now read Qlib's learn view; the
validation and test paths read feature-only inference views. Admission,
mature-label checks, purge/embargo, Top-20 target generation and A-share
execution remain project-owned. The merged PR passed 286 synthetic tests;
the PR CI did not install real Qlib.

### Phase 9.8 — real Qlib 0.9.7 API compatibility (merged in PR #68)

The dedicated Python 3.12.16 workflow installed Qlib 0.9.7, Pandas 2.3.3 and
LightGBM 4.7.0. The synthetic-only smoke test passed against the real
`DataHandlerLP.from_df`, `DatasetH.prepare`, learn/infer views, pandas datetime
MultiIndex and LightGBM Dataset constructor. `lightgbm.train` was stubbed; no
model was fitted. The job did not access the local data lake, call CNEquity,
run a real-data backtest or alter base dependencies/version locks.

### Phase 9.9 — full synthetic walk-forward integration (merged in PR #69)

Run the existing model runner with the real Qlib 0.9.7 DatasetH and LightGBM
Dataset interfaces, real per-fold project admission, validation and test
prediction, Top-20 equal-weight targets and the shared project backtest engine.
The Qlib-specific workflow replaces only `lightgbm.train` with a deterministic
prediction-only Booster. No model optimization, real data, CNEquity call or
empirical backtest is performed.

### Phase 9.10 — matured validation ranking diagnostics (merged in PR #70)

The diagnostic model's feature-only validation predictions are joined to
validation forward-return labels only after those labels mature strictly before
the fold test start. Per signal week, the runner records Spearman rank IC,
Pearson IC, Top-N mean return and excess versus the labeled cross-section, and
an equal-sized Top-Bottom spread. Fold and aggregate summaries are persisted.
Test labels are rejected, and no automatic hyperparameter tuning or model
selection is performed from these metrics. The valid-IC count includes negative
and zero observations; positive IC ratio uses all defined IC observations.

### Phase 9.11 — cross-fold validation stability and explicit acceptance policy (merged in PR #71)

The runner summarizes weekly validation Rank IC across folds, including
mean/median/sample standard deviation, descriptive ICIR, positive-week ratio,
positive-mean-IC fold ratio, Top-N excess stability and pooled label coverage.
An optional `ValidationRankingAcceptancePolicy` applies predeclared thresholds.
There are no implicit financial cutoffs: the caller must supply a policy ID and
minimum fold/date/valid-IC/cross-section/coverage/ranking thresholds. The exact
policy values and SHA-256 fingerprint are persisted in the aggregate report
and fold provenance. Without a policy, the decision remains `NOT_ASSESSED`;
insufficient sample or coverage yields `INSUFFICIENT_EVIDENCE`.

Latest Phase 9.11 validation: base CI 305 passed, 2 skipped; the dedicated
Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0 integration job
passed 2 tests. The optimizer was stubbed; no real model fit, local data-lake
access, CNEquity call, or empirical market backtest was performed. Validation
policy acceptance is not a claim of test-period profitability.

See [validation_ranking_policy.md](validation_ranking_policy.md).

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

### Phase 9.12 — next-open execution-aligned labels (merged PR #72)

The prior label formula used signal-day adjusted close to the close five
sessions later, while the engine can only buy at the next session open. The
new label builder derives HFQ adjusted opens from each session's raw open and
same-session adjusted-close/raw-close multiplier and requires exact HFQ
evidence. It measures the return from the first executable open after a
signal to the first executable open after the next weekly signal, stores both
entry and maturity dates, and follows holiday-shortened weeks. Admission
requires the explicit entry date and validates it strictly between signal
and exit maturity. Walk-forward purge adds the one-session entry lag to the
open-to-open holding horizon before the configured embargo. Folded momentum
replays retain weekly signal dates from the original full calendar rather
than inventing a rebalance at a midweek cutoff. No real training or market
run is performed.


### Phase 9.13 — fail-fast execution-label contract (merged PR #73)

The model runner now requires `label_entry_date` in its public label schema
before fold planning, admission, Qlib dataset construction, or model fitting.
Legacy label frames lacking the executable entry date fail at the runner
boundary with a specific contract error. PR #73 CI: 314 unit tests passed,
2 skipped; real Qlib/LightGBM synthetic API smoke tests passed (2). No
optimizer fit or empirical market run was performed.


### Phase 9.14 — exact HFQ and deterministic PIT feature inputs (merged PR #74)

The cross-sectional feature builder now requires the adjusted-close exactness
flag and rejects any null, non-finite, non-positive or non-exact HFQ rows.
Strict PIT facts must have a non-empty report period and finite numeric value;
duplicate (instrument, item, report period, availability timestamp) facts
fail closed instead of relying on provider row order. Feature calendars must
already be chronologically ordered. PR #74 CI: 321 unit tests passed,
2 skipped. No Qlib/LightGBM API, real training or market backtest was changed
or run.


### Phase 9.15 — enforce execution entry dates at low-level fit boundaries (merged PR #75)

The direct LightGBM fit adapter now requires canonical signal, entry and
maturity dates and enforces
`signal_date < label_entry_date < label_end_date < training_cutoff`.
The Qlib panel bridge independently requires and validates the entry date
before converting rows; Qlib dataset wrappers carry a verified-entry-contract
marker, and the Qlib LightGBM adapter rejects wrappers that have not passed
the project conversion path. The walk-forward runner also verifies entry and
maturity dates against the bundle's official session calendar. Synthetic
tests cover legacy labels, invalid entry ordering, non-canonical dates,
unverified Qlib wrappers and non-session execution dates. PR #75 CI:
331 unit tests passed, 2 skipped; the Python 3.12.16 / Qlib 0.9.7 /
Pandas 2.3.3 / LightGBM 4.7.0 real-API synthetic workflow passed 2 tests.
The LightGBM optimizer remained stubbed; no real model fit or market
backtest was performed. Merge commit: `728016f`.



### Phase 9.16 — latest-known PIT report-period precedence (merged PR #76)

PIT feature selection now first chooses the newest report period already
available at each signal timestamp, then chooses the latest revision within
that report period. A later restatement of an older period cannot override a
newer period already published. Report periods must be canonical YYYY-MM-DD
dates and cannot be later than their publication date. Synthetic regression
tests cover older-period restatements, same-period revisions, malformed dates
and future report periods. PR #76 CI: 336 unit tests passed, 2 skipped.
No real model fit or market backtest was performed. Merge commit:
`1d235523a6a61f286769ec89b037860fd0d153fd`.


### Phase 9.17 — finite-safe zero-volume activity features (merged PR #77)

The rolling volume-ratio feature now returns null when the rolling mean
volume is zero or unavailable. Zero-volume observations remain in the raw
panel; the feature builder no longer creates NaN from a 0/0 ratio. Training
and inference therefore both exclude incomplete rows instead of allowing a
single NaN to invalidate an entire inference cross-section. Synthetic CI:
337 unit tests passed, 2 skipped. No real model fit or market backtest was
performed. Merge commit: `c8e94f10154710be78044bf4c55425798504ed6e`.


### Phase 9.18 — consistent finite-feature inference eligibility

Qlib inference-panel construction and the direct model prediction adapter now
use the same row eligibility rule as supervised training: every configured
feature must be present and finite. Null, NaN, positive-infinity and
negative-infinity feature rows are excluded before prediction rather than
allowing one incomplete security to invalidate the entire weekly cross-section.
The model's keyed predictions retain only eligible instruments, and the shared
Top-20 selector ranks that available set without imputation. Synthetic tests
cover all four invalid-value cases in both inference adapters. No model fitting,
real market backtest, local data-lake access or CNEquity operation is performed.
PR #78 is merged. Final-head CI: 339 unit tests passed, 2 skipped;
the Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0 synthetic
compatibility workflow passed 2 tests. Merge commit:
`e68c0d27a4d488dcba3f57df39e39dc539aa326e`. No optimizer fit, real-data
training or empirical market backtest was performed.


### Phase 9.19 — enforce PIT universe at the walk-forward boundary

The model runner now validates every caller-supplied feature and label key
against the validated bundle's same-date point-in-time membership snapshot.
A frame cannot introduce a security outside the historical eligible universe
even if it bypasses the canonical feature builder. The failure occurs before
fold planning, admission, or model fitting. A synthetic regression test adds
an out-of-universe instrument and confirms the runner rejects the frame at
its public boundary. No empirical data, model fit, or market backtest is used.

PR #79 is merged. Final-head CI: 339 unit tests passed, 2 skipped;
the Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0 synthetic
compatibility workflow passed 2 tests. Merge commit:
`bec4a49de7427d862eda6c89df99d39e3288ec0a`. No optimizer fit, real-data
training or empirical market backtest was performed.


### Phase 9.20 — preserve missing PIT fundamentals with native missing values

The earlier all-features-complete rule removed an otherwise eligible security
when a strict ROE/net-profit fact was not yet available at that historical
signal close. Model feature eligibility is now type-aware: all market-derived
features must be non-null and finite, while a `pit_*` fundamental may remain
null if no eligible fact was knowable then. Only true PIT nulls are transported
as NaN to LightGBM; source NaN/Inf values remain invalid. Both direct and Qlib
training/inference paths explicitly enable LightGBM native missing handling
(`use_missing=true`, `zero_as_missing=false`), without zero-fill or
forward-fill.

Synthetic unit tests cover row eligibility and direct adapter matrices. The
dedicated Python 3.12 Qlib 0.9.7 / LightGBM 4.7.0 compatibility workflow also
verifies that Qlib preserves null PIT values through DatasetH learn/infer
views and that the real LightGBM Dataset constructor accepts those NaNs; the
optimizer remains stubbed. No real data, optimizer fit or market backtest is
performed. This refines the all-feature-complete eligibility policy recorded
under Phase 9.18.

PR #80 is merged. Final-head CI: 344 unit tests passed, 2 skipped; the
Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0 compatibility
workflow passed 2 synthetic API tests. The compatibility job verified
DatasetH learn/inference views and LightGBM Dataset construction, while
stubbing `lightgbm.train`; no optimizer fit, real-data training, local
data-lake access or empirical market backtest was performed. Merge commit:
`3b60cd624bc1e3dfcafddb8a2e46b80ea7091a14`.

### Phase 9.21 — diagnose PIT missingness and Top-20 coverage

The walk-forward runner now emits post-prediction validation diagnostics by
PIT item and for the combined any-PIT-missing group. Reports separate the
full signal-date PIT-universe denominator from the market-feature-eligible
prediction denominator, then measure Top-20 missingness and compare prediction
scores plus matured validation returns for missing versus observed PIT groups.
Validation labels are accepted only after maturity strictly before the fold's
test start; these diagnostics are descriptive and never tune the model.

Untouched test/OOS folds use a separate label-free coverage path. It accepts
only the full feature frame and keyed predictions, and reports per-date
universe/prediction coverage plus Top-20 PIT missingness. It has no label
argument and emits no return/outcome metrics. Validation outcome diagnostics
and test/OOS coverage are persisted as separate Parquet artifacts and are
summarized separately in fold and aggregate reports.

Market-cap, listing-age and industry slices are explicitly deferred because
the canonical validated bundle does not yet carry receipt-backed,
point-in-time metadata with historical effective dates. No instrument-code or
price/turnover proxy is inferred.

PR #81 is merged. Final-head GitHub Actions: 351 unit tests passed, 2 skipped;
the Python 3.12.16 / Qlib 0.9.7 / Pandas 2.3.3 / LightGBM 4.7.0 real-API
compatibility workflow passed 2 synthetic tests. The compatibility workflow
exercised the Qlib DatasetH and LightGBM bridge with the final validation/OOS
diagnostic integration; it did not run LightGBM optimization or fit a model.
No local data-lake access, CNEquity queries/backfills, real-data training or
empirical market backtest was performed. Merge commit:
`c5667a06c5209a118c6ef67a7f58791cba479253`.

### Phase 9.22 — define receipt-bound PIT security metadata contract

A provider-agnostic, fail-closed metadata contract now defines typed
observations for immutable listing-date events, effective-dated industry
classifications, and same-session market capitalization. Every observation
requires an effective interval, a timezone-aware availability timestamp,
source revision, contract fingerprint, receipt ID and source-certified
evidence capability. Selection resolves revisions only as knowable at the
Shanghai signal cutoff, forbids market-cap carry-forward, and stamps the
selected snapshot with its signal date and decision timestamp. Coverage
auditing requires every requested signal-universe instrument/field pair and
fails on missing or unexpected rows.

Industry classification systems are explicit and isolated: EastMoney and
Shenwan codes cannot be merged into one taxonomy. Field units are fixed by
the schema (date, classification code or CNY), and coverage audits reject
unknown fields and mixed taxonomies.

The locked CNEquity 0.21.0 contract and the reviewed 0.23.0 contract retain
material source limitations: industry_members and valuation_metrics are
non-PIT snapshots; share_structure is partial/reconstructed PIT; instruments
listing dates remain only conditional immutable-event candidates pending
source verification and complete historical-universe coverage. The selector
is not wired into the feature builder, admission or model runner. No
market-cap, listing-age or industry feature is enabled by this phase.

PR #82 is merged. Final-head GitHub Actions: 374 unit tests passed, 2 skipped.
These are synthetic contract/temporal-boundary tests only. No local data-lake
access, CNEquity reads/queries/backfills, real-data training, optimizer fit
or empirical market backtest was performed. No dependency or version-lock
change was made. Merge commit:
`a93291fb464b89c417de97868da2d42ca200ce17`.



### Phase 9.23 — bind PIT metadata capabilities to field-semantic audits

The Phase 9.22 metadata source identity previously accepted a flat
`validated_evidence_classes` list alongside a receipt ID. The list was not
itself tied to the actual receipt dependencies or scoped to individual fields.
This phase replaces that shortcut with
`build_security_metadata_source_identity(...)`: it requires a replayable
receipt that pins the dataset's exact revision and contract fingerprint,
plus PASS field-semantic audit records bound to the same receipt and revision.
Each audit records a unique audit ID, dataset, evidence class, field scope,
method and timezone-aware audit timestamp. Validation checks that every
audited field is permitted for that dataset and evidence class, and each
observation must have a matching field-scoped capability.

This is still a structural contract, not an authenticated external audit:
the helper does not establish auditor identity or prove a provider's actual
historical coverage. Production audit IDs must refer to independently retained
source-qualification evidence. No metadata source has been promoted, and the
metadata selector remains outside the feature builder, bundle admission and
walk-forward runner.

Synthetic CI covers missing/legacy capability declarations, receipt/revision
drift, field-scope mismatch and normal as-of behavior. No local data-lake
access, CNEquity query/backfill, model fit or empirical backtest is performed.


### Phase 9.24 — qualify listing-date evidence sources before feature use

Reviewed official SSE, SZSE and BSE public listing-event and archive material,
plus the public contract for a third-party A-share basic-information API.
The SSE monthly archive/report provides code, name and listing-date entries,
but the displayed archive and period table do not establish complete
1990-to-present, delisted-symbol coverage or first publication time. The SZSE
announcement archive provides event-level listing and termination notices;
its sample exposes a difference between the page publication date and the
document signature date, so `available_at` cannot be inferred from the
document footer. BSE old/new code mapping helps with identifier reconciliation,
but migrated Select-Layer companies have a special legal listing-time
continuity rule that must be resolved before a migrated code's date can be
admitted. CnOpenData publicly claims tri-exchange coverage back to 1990-12 and
delisted-security records, but its public API description does not expose
row-level original document provenance, first-publication timestamps or
replayable historical row revisions.

The immutable listing-date contract now allows the future
`sse_listing_events`, `szse_listing_events` and `bse_listing_events`
dataset namespaces. This is only a schema capability path: no source adapter
is registered and no source is qualified. Added a source evidence envelope,
availability-time rule, full-universe reconciliation gates, BSE migration
edge-case policy and public source references in
`docs/listing_date_source_qualification.md`.

Synthetic tests verify that the three exchange-specific namespaces can
validate a receipt-bound immutable listing-date observation. They do not test
real exchange endpoints or establish provider completeness. No API was
invoked, no data rows were downloaded, no local data-lake access or CNEquity
query/backfill occurred, and no real training, optimizer fit or empirical
backtest was run. No dependency or CNEquity version-lock change was made.


### Phase 9.25 — executable listing-event evidence envelope

Added the pure `validate_listing_event_evidence(...)` validator for exchange
listing-event document records. It requires an exchange-qualified symbol,
initial-listing event type, exchange-specific dataset namespace, official
HTTPS host, document ID/kind, retained-document SHA-256, page/table/row
locator, publication date and precision, a separate document/signature date,
published/available/captured timestamps, explicit BSE transfer classification,
and receipt/revision/contract provenance.

Date-only publication is conservatively cut off at 23:59:59 Asia/Shanghai;
`available_at` must equal the actual documented publication time. Document
signature date is never substituted for public release time. Precise
publication timestamps must include timezone. BSE Select-Layer transfer
records fail closed until the legal listing-age start is proven separately
from the code migration date. Evidence rows are then checked by the Phase 9.23
receipt-bound, field-scoped immutable-event metadata contract.

Synthetic tests cover all three exchange namespaces, official domain and code
matching, date/timestamp publication semantics, digest/locator validation,
receipt drift, duplicate evidence and BSE transfer rejection. This validator
does not fetch URLs, hash external documents, authenticate auditors, or prove
full PIT-universe coverage. It is not wired into bundle assembly, features,
training admission or walk-forward. No source API or data rows were accessed;
no local lake read, CNEquity query/backfill, real training, optimizer fit or
empirical backtest was run. No dependency or version-lock changes were made.

### Phase 9.26 — signal-date listing-event availability audit

Added `audit_listing_event_signal_coverage(...)` on top of the existing
receipt-bound listing-event evidence validator. For one supplied historical
membership snapshot and one decision cutoff, it reports missing event evidence,
evidence published after the decision cutoff, and listing dates after the
signal date, with exchange-level breakdowns. Date-only publication is
conservatively available at 23:59:59 Asia/Shanghai. The audit explicitly does
not prove that the supplied universe is complete, validate delisting history,
or unlock training/backtesting.

The synthetic test suite passed in CI (426 passed, 2 skipped). This is code
validation only; no exchange API call or real data test was run.

### Phase 9.27 — official announcement source and API evaluation

The 2026-11-16 recheck identified an SSE InfoNet Announcement File Service.
Its older 2022 product description lists SSE main board, SSE STAR Market, and
SZSE stock announcements, with date-queryable metadata and unique
index/download URL identifiers. The provider's current product overview,
however, describes Shanghai-market stock/bond/fund announcements, regulatory
inquiries, and issuance/listing announcements without expressly confirming
current SZSE coverage. This scope discrepancy must be confirmed before the
service can be treated as an SH/SZ source.

The provider's current public price list states CNY 60,000 per year for
Announcement File Service. Its current application guide says application,
approval, and signing of a data-service agreement; the older product
description mentioned a trial before contracting, so current trial
availability is unconfirmed. The public material still does not establish
2018-to-present historical retention, delisted-symbol completeness,
original-publication timestamp semantics, or complete revision replay.
Therefore no adapter is registered, no provider is admitted, and no purchase
is recommended before a bounded historical proof.

The public SZSE company-notice archive has numbered pages reaching at least
`index_7` (observed dates June-August 2024); individual 2020 delisting
notices remain accessible. The SSE listing/delisting entry is visible but
includes bond listing notices in the reviewed listing, so stock-only event
filters remain to be proven. These are interface observations, not proof of
historical archive completeness.

The next gate is a bounded historical proof using either an authorized InfoNet
trial/specification or exhaustive public-page capture. The proof must reconcile
all events and symbols in the target interval, distinguish original publication
from later update time, retain original bytes/digests, and create replayable
receipts. Third-party reverse-engineered endpoints remain experimental leads
only. No API was invoked, no credentials or paid service were acquired, no data
was downloaded, and no local lake read, CNEquity query/backfill, training or
real backtest was performed.

### Phase 9.28 — CNINFO as a public listing-event source candidate

The current CNINFO site identifies itself as the SZSE official disclosure
website operated by an SZSE wholly owned subsidiary. Its public navigation
covers both Shanghai and Shenzhen boards and includes a termination/delisting
section. The CSRC's 2009 designation explicitly names CNINFO as one of five
ChiNext legal-disclosure websites; this is board-specific evidence and is not
extrapolated to every SSE/SZSE board or to complete archive retention.

A third-party maintained client documents a reverse-engineered POST query at
`/new/hisAnnouncement/query`, with date range, page, exchange/board and
keyword/category parameters, response counts, announcement IDs and PDF URLs.
This is a protocol research lead, not an official API contract. An observed
millisecond `announcementTime` can be a date stamp at 00:00 Asia/Shanghai;
until original first-publication semantics are established, it must not be
treated as an exact intraday timestamp.

The team should qualify the public CNINFO route before considering the
CNY 60,000/year SSE InfoNet Announcement File Service. The initial probe must
be small, read-only, rate-limited, and retain raw request/response captures,
hashes, page totals, stable announcement identity, original files and
publication-time semantics. It must sample listing and termination events in
SSE main, SSE STAR, SZSE main and ChiNext, and include delisted issuers.
A capped or partial query is never a completeness PASS.

No CNINFO endpoint was called and no sample data were collected in this phase.
No production adapter, source receipt, PIT listing-date admission, training
feature, or backtest input was enabled. See
`docs/listing_date_source_qualification.md` for the bounded qualification
protocol and source limitations.

### Phase 9.29 — bounded, opt-in CNINFO metadata probe

Added `scripts/probe_cninfo_announcement_api.py` and the
`ashare_quant.research.cninfo_probe` module. Without `--execute`, the CLI
only validates and prints a request plan and performs no network access. With
the explicit flag, it bootstraps the CNINFO search page and issues a small,
rate-limited number of form-encoded POST requests to the reverse-engineered
announcement query route.

The probe limits each date interval to 31 calendar days and each run to at most
10 result pages (default 2). It captures raw bootstrap/JSON response bytes,
query parameters, request and capture times, SHA-256 hashes, announcement
identity-field missingness, duplicate IDs, and consistency between page/record
counters. It never downloads announcement PDFs, accesses the local CNEquity
lake, writes production datasets, or unlocks PIT/training/backtesting. A
complete traversal means only that the server-reported pages for that one query
were captured; it is not historical source coverage.

Synthetic tests cover no-network planning, complete query traversal, a page-cap
PARTIAL result, duplicate IDs, no-result responses, date-window bounds, and
missing announcement identity fields. CI validates the code only. The live
CNINFO endpoint has not been called from the project CI environment; local
network verification is still pending before any adapter or source qualification.

### Phase 9.30 — reconcile CNINFO zero-page counter with live result evidence

The first operator-run live request reached the CNINFO search page and
announcement query endpoint successfully (HTTP 200 on both). The selected
SZSE query for 2020-05-22 and keyword `终止上市` returned five rows, five
unique announcement IDs, and no missing required identity fields. CNINFO
reported `totalRecordNum=5` and `hasMore=false`, while
`totalpages=0`; the original probe treated that contradictory page counter
as PARTIAL.

The parser correction preserves the raw `totalpages=0`, derives an effective
one-page expectation from the positive record count and requested page size,
and allows `COMPLETE_WITH_WARNINGS` only when the captured row count matches
the reported total and `hasMore=false`. Record-count mismatch or a claim of
more pages remains PARTIAL. New synthetic tests cover the observed counter
pattern and its fail-closed variants.

All five returned announcement-time values were midnight-valued. They remain
date-stamp candidates only, not exact first-publication timestamps. The live
response proves connectivity and returns metadata for this one query; it does
not prove archive completeness, correct event classification for every row,
or historical universe/PIT admission. The original raw `page_001.json` still
needs row-level inspection before expanding the probe.

### Phase 9.31 — complete multi-page reconciliation when CNINFO reports zero pages

A follow-up code review found that Phase 9.30 derived an effective page count from `totalRecordNum/pageSize` for a raw `totalpages=0` response, but the final traversal check still compared captured pages with the raw counter. A fully captured multi-page response could therefore be incorrectly marked PARTIAL.

The final completion check now uses `effective_expected_pages` while preserving the raw `totalpages=0` value and its warning. Added a two-page synthetic regression case with 31 records, page size 30, `hasMore=true` on page 1 and `hasMore=false` on page 2. Counter mismatches, premature termination and page-cap truncation remain fail-closed.

PR #97 was merged as `f7d93aa57cdb743a8b551a1f1b8ced504eaea914`. GitHub Actions Run #380 passed **437 tests, with 2 skipped**. These are synthetic code tests only.

A public control-sample review also confirmed the SZSE *ST龙力 termination decision dated 2020-05-22 and a CNINFO-hosted PDF candidate for that event. The exact match between that document and one of the operator's five captured API rows remains pending review of `page_001.json`; the PDF filename is not treated as the API announcement ID. No announcement adapter, source coverage, PIT availability, training or backtest gate was promoted.

### Phase 9.32 — distinguish termination risk warnings from completed listing events

The operator supplied the complete raw CNINFO response for the 2020-05-22
Shenzhen `终止上市` keyword query. The five returned rows cover ST天宝
(002220), 盛运环保 (300090) and 神雾环保 (300156). All five titles are
risk warnings about a possible suspension/termination or possible delisting
because the share price is below par. No row is the *ST龙力 (002604)
termination decision recorded by SZSE for the same date.

The sample proves that the keyword endpoint can return relevant historical
announcement metadata, but it also demonstrates a major classification
hazard: a full-text/title hit on `终止上市` is not equivalent to a completed
delisting event. The `plate=sz` query returned both `SZZX` and `SZCY`
page-column values, so the selector's exact board semantics remain unqualified.
All five announcement-time fields are the same midnight timestamp and do not
establish first-publication time.

Added `classify_cninfo_announcement_row(...)`, row-level classification
summaries in probe reports, and the offline
`scripts/analyze_cninfo_announcement_capture.py` utility for reviewing
previously captured JSON without network calls or PDF downloads. Risk-warning
titles cannot be promoted to decision/result events. Decision/result-looking
titles remain candidates until the original document and exchange decision are
reviewed.

The original PDF for 神雾环保 row `1207844344` was verified as a seventh
risk-warning notice, not a completed delisting decision. The exact *ST龙力
API-row match remains pending a focused one-day `龙力` keyword probe and
row-level reconciliation. No listing-date source, PIT availability,
historical-universe coverage, training or backtest gate was promoted.

### Phase 9.33 — retain auxiliary CNINFO result counters without over-interpreting them

The 2020-05-22 five-row response reported `totalAnnouncement=5`,
`totalRecordNum=5`, but `totalSecurities=0`; the captured rows contain
three distinct security codes. The row-level `id` field is null while
`announcementId` is populated and unique. Because the reverse-engineered
protocol does not define the semantics of `totalSecurities`, the probe now
preserves this raw counter and separately reports the observed distinct
security-code count. It does not assert that the two counts must be equal or
use `totalSecurities` for universe coverage. Auxiliary counter changes
between pages are retained as query warnings. Synthetic tests cover the
zero-counter / three-observed-codes case.

No source-coverage, PIT, training or backtest admission is changed.

### Phase 9.34 — qualify SZSE exchange-issued delisting decision evidence

The operator completed four further bounded CNINFO query variants for the
known *ST龙力 (002604) termination decision dated 2020-05-22: keyword
`龙力`; stock code with the current CNINFO orgId; and stock code/current orgId
with the historical SZSE SME-board selector `szzx;`. All returned a 165-byte
zero-result JSON body with the same SHA-256
`c2a890bbf3a6a53ab02ddc6c1794bf1c72ba45799fe9f59dcc5ba2cc18467114`, although
the request bodies differed. The top-search identity endpoint returned the
current mapping `002604 / 9900021073 / 龙力退 / delisted=true`. That current
alias/orgId is not evidence of the 2020 historical identity.

The identical empty result across distinct filters means this reverse-engineered
query route did not provide discriminating evidence for the known event. It
does not prove the event is absent from CNINFO static archives or explain
whether the limitation is historical indexing, a hidden session prerequisite
or a request-protocol mismatch. Retain the probe as research-only; do not use
`NO_RESULTS` as an archive-completeness finding or as a veto against an
official event record.

The SZSE original exchange-issued notice was independently located at
`https://www.szse.cn/disclosure/notice/t20200522_577530.html`. It identifies
the 2020-05-22 decision to terminate listing of 山东龙力生物科技股份有限公司
(*ST龙力, 002604) and states that the stock would enter the 30-trading-day
delisting consolidation period on 2020-06-01. The issuer-authored notice
published on 2020-05-23 independently confirms receipt of the exchange
decision on 2020-05-22 and cites 深证上〔2020〕428号. These are different
records with different publishers and publication dates.

The source-selection decision is to use the SZSE `/disclosure/notice/`
**本所公告** record as the primary event evidence for SZSE termination
decisions, and the issuer notice as corroboration. Do not conflate this with
the `/www/disclosure/notice/company/` issuer-notice archive or the
`/marketServices/deal/period/delisted/` post-delisting announcement
collection. Keep decision date, delisting-consolidation start, last trading
date, final delisting-effective date and issuer notice date as separate
fields. The exchange page exposes date-level publication metadata only;
until exact release time is established, use the conservative
23:59:59 Asia/Shanghai availability cutoff for that publication date.

Added `docs/delisting_event_source_qualification.md` with the case evidence,
CNINFO probe interpretation, source hierarchy, temporal/event field semantics
and full production acceptance gates. This verifies one official case only;
SZSE archive discovery, historical-universe reconciliation, original-byte
capture, replayable receipts and complete delisting-event coverage remain
unqualified. No production adapter, source receipt, PIT admission, CNEquity
change/read/backfill, training or real backtest was enabled.

### Phase 9.35 — bounded SZSE 本所公告 HTML capture

Added a read-only, opt-in capture module and CLI for the publicly indexed
SZSE 本所公告 list-page sequence and individual notice pages. The observed
list routes include `index.html` and numbered `index_N.html` pages; notice
detail routes use `tYYYYMMDD_ID.html`. The code treats these as observed
public-page conventions, not an official API contract.

The capture is capped at 10 listing pages and 50 notice pages per run, enforces
at least 0.5 seconds between requests, limits each HTML body to 20 MiB, and
only follows HTTPS HTML URLs on `www.szse.cn`. It preserves the original
listing/article HTML bytes, URLs, request/capture timestamps, SHA-256 hashes,
record IDs and extracted title/date observations. It never follows PDF
attachments. Operator-supplied article URLs are explicitly labeled as seeds
and prioritized under the article cap rather than being misrepresented as
archive-discovered records.

Added `scripts/capture_szse_notice_archive.py`,
`src/ashare_quant/research/szse_notice_archive.py`,
`tests/unit/test_szse_notice_archive.py` and
`docs/szse_notice_archive_capture.md`. The parser supports regular article
anchors and observed inline `curHref`/`curTitle` metadata. Synthetic tests
cover parsing, official-host allowlisting, redirects, response hashes, bounded
options and explicit seed labeling.

This phase captures a bounded HTML sample only. The numbered-page convention
does not prove the archive's historical pagination boundary or availability of
all years; the current list pages may be recent-only. A zero-link parse is not
a zero-event archive result. Historical date-range discovery, full pagination
reconciliation, original attachment retention, full SZSE-universe reconciliation,
row-level receipts and PIT/production admission remain unqualified. No CNEquity
change/read/backfill, local data-lake access, production data write, training
or real backtest was enabled.

### Phase 9.36 — offline SZSE 本所公告 HTML structure inspection

Added an offline inspector for previously captured official SZSE HTML. It records
form actions/methods, visible input/select/button names and placeholders,
first-party script references, inline-script SHA-256 values, literal route
candidates and query-parameter names. Hidden input values are excluded, raw
capture bytes are not changed, external scripts are not fetched and JavaScript
is not executed. The utility has a 20 MiB input cap and produces a structured
JSON inspection report.

This phase is diagnostic only. Candidate route strings do not constitute an
API contract; server-side search must not be implemented until the relevant
first-party script/request shape is captured and reviewed. The existing
`--keyword` and date-bound options remain local filters over bounded captured
list-page candidates. No live query request, CNEquity access, local data-lake
read, production write, backfill, training or real backtest is enabled.

