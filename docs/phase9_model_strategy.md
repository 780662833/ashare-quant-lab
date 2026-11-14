# Phase 9 — cross-sectional model strategy

## Purpose and boundaries

Phase 9 implements the first learned cross-sectional selection strategy. It
must predict forward returns from information available at each weekly close,
rank the eligible point-in-time universe, and emit target weights only. Orders,
cash, fills, costs and NAV remain owned by the existing shared backtest engine.

The benchmark remains the fixed 126-session / skip-21 momentum strategy. It is
not the model strategy and must not be used as a proxy for one.

## Frozen v1 model contract

- Decision: after the final supplied exchange session in each ISO week.
- Execution: no earlier than the next eligible session open.
- Universe: SH/SZ research profile, membership as-of each signal date.
- The public walk-forward runner rechecks every supplied feature and label
  `(signal_date, instrument)` key against the validated bundle's exact
  same-date membership snapshot before planning folds or admitting training.
- Portfolio: top 20 predicted names, equal weight, no leverage.
- First learner: LightGBM regression, trained only on chronological folds.
- First label: exact HFQ open-to-open return from the next session's executable
  open to the first executable open after the following weekly signal. The
  official exchange calendar determines the holding window, including holiday
  shortening.
- Benchmark: existing 126/21 weekly momentum strategy.
- No feature may use information published after the signal close.
- No label may enter training until its label_end_date is before the fold's
  training cutoff; purge and embargo remain explicit.

## Initial feature set

| Feature | Definition | Source / time rule |
|---|---|---|
| return_5 | HFQ close(t) / HFQ close(t-5) - 1 | Exact official-session endpoints |
| return_20 | HFQ close(t) / HFQ close(t-20) - 1 | Exact official-session endpoints |
| return_60 | HFQ close(t) / HFQ close(t-60) - 1 | Exact official-session endpoints |
| return_120 | HFQ close(t) / HFQ close(t-120) - 1 | Exact official-session endpoints |
| volatility_20 | 20-session rolling standard deviation of daily HFQ returns | Missing sessions remain null |
| avg_amount_20 | 20-session mean raw traded amount | Execution bars, no fill-forward |
| volume_ratio_20 | Current raw volume / 20-session mean volume | Execution bars, no fill-forward |
| pit_roe | Latest strict ROE fact available by signal close | Strict, exact PIT fact |
| pit_net_profit | Latest strict net-profit fact available by signal close | Strict, exact PIT fact |

Feature windows are exchange-session counts, not calendar-day counts and not
counts of observed rows for an individual security. The builder constructs an
explicit instrument/session grid, so a missing bar does not compress time or
get forward-filled. Early-window and missing-input values remain null; any
future training-row eligibility policy must be explicit and recorded.

The initial set is deliberately small and auditable. Do not add a large factor
zoo, technical-indicator library, or tuned parameter sweep to v1 before the
baseline is validated.

## Label contract

For signal date t, let entry be the first official exchange session after t,
and let exit be the first official exchange session after the next weekly
signal date:

forward_return = HFQ_adjusted_open(exit) / HFQ_adjusted_open(entry) - 1

Each adjusted open is reconstructed from that session's raw open and exact
same-session HFQ multiplier (adjusted close divided by raw close). Both
execution endpoints must exist exactly; no price or factor is forward-filled.
The label row carries label_entry_date and label_end_date, where the latter is
the actual exit execution/maturity date. Holiday-shortened weeks follow the
official exchange calendar, not a fixed five-session offset. Rows without an
executable endpoint are omitted. Membership is frozen as-of signal date; the
future universe is never used to decide which historical names receive labels.
Label maturity must be checked against fold cutoffs before any training or
validation use.

## PIT fundamental handling

Input rows must carry instrument, item, report_period, available_at, value,
pit_quality, and pit_is_exact. Only pit_quality='strict' and
pit_is_exact=true facts are eligible. `assemble_cnequity_pit_fundamentals(...)`
reads the receipt-backed CNEquity financial_statement_items dataset and
normalizes symbol/item_code/item_value/announce_date. Because announce_date
may contain only a date, the adapter conservatively sets availability to
23:59:59 Asia/Shanghai on that date; same-day after-close publication can
therefore never leak into that day's 16:00 signal. The helper pins the PIT
adapter receipt and revision into bundle provenance. Admission requires the
acceptance report's PIT receipt and the actual feature-input adapter receipt to
pin the same financial_statement_items revision and contract fingerprint. The
feature builder then selects the latest known report for each instrument/item. A report period is
never treated as its publication timestamp.

## Training admission — implemented, model fitting still gated

`admit_training_dataset(...)` is a pure, fail-closed decision function. It
requires an exact acceptance window and universe profile, a stable acceptance
report ID, matching replayable source revisions/receipts, and PASS status for
the five report evidence entries: historical universe, replayable universe
receipt, HFQ, strict PIT fundamentals and trading status. It also validates
numeric feature columns, unique signal/instrument keys, feature/label key
alignment and strict label maturity before the training cutoff.

The admission object is auditable evidence, not a model object and not a
training command. The acceptance report and provenance continue to carry
`training_dataset_unlocked=false`. A dedicated model runner must consume the
admission decision and enforce it before any LightGBM fit. No training has been
started.

## Qlib DatasetH, LightGBM and portfolio targets

The primary walk-forward training path is Qlib `DatasetH` ->
`LightGBMRegressionModel`, not a separate Qlib model stack:

- `create_qlib_dataset(...)` accepts only the exact successful fold admission,
  checks feature/label fingerprints and maturity, and creates a Qlib
  `DatasetH` bundle. Validation and test frames enter only as feature-only
  inference rows; their labels are not sent to the inference view.
- The diagnostic fit reads Qlib's train segment through
  `DataHandlerLP.DK_L`; validation predictions read only Qlib's feature
  inference view through `DataHandlerLP.DK_I`.
- Before test prediction, the runner re-admits train-plus-validation rows at
  the test cutoff. Only labels matured strictly before that cutoff enter the
  final Qlib train segment. The test segment is feature-only.
- `fit_qlib_lightgbm_regressor(...)` uses the frozen deterministic LightGBM
  regression configuration and returns the same project model type used for
  model persistence. The lower-level Polars fit adapter remains available for
  isolated compatibility tests; the walk-forward runner uses Qlib.
- `weekly_cross_sectional_model_targets(...)` ranks each signal-date
  cross-section by the predicted execution-aligned weekly open-to-open return
  and delegates Top-20 equal-weight target construction to the shared engine.
- Training and inference use one explicit feature-eligibility rule:
  market-derived features must be non-null and finite; `pit_*` fundamentals
  may remain null when no strict fact was knowable at the signal close.
  Only true null PIT values are converted to NaN for LightGBM's native
  missing-value handling (`use_missing=true`, `zero_as_missing=false`).
  Source NaN/Inf values and missing market features remain ineligible; no
  imputation, zero-fill or forward-fill is performed. This reduces row loss
  caused only by unavailable point-in-time fundamentals without introducing
  future information.
- Validation folds also emit post-prediction PIT missingness diagnostics. These
  report per-item and any-PIT-missing coverage, model-eligible prediction
  coverage, Top-20 missingness, and matured validation return/score comparisons.
  They are descriptive only and never tune the model. Market-cap, listing-age,
  and industry slices remain deferred until the canonical bundle has a
  receipt-backed point-in-time metadata dimension.
- Untouched test folds also emit a separate label-free feature-coverage artifact.
  It reports per-date PIT-universe versus prediction coverage and Top-20 PIT
  missingness only; test labels and outcomes are never passed to this path.

Qlib segment selection does not replace the project's walk-forward planner,
label-horizon purge, embargo or admission. PIT availability, source receipts,
historical universe, trading status and feature construction remain
project-owned. T+1, order eligibility, price limits, commissions, transfer
fees, stamp duty, slippage, fills and unfilled-order handling also remain
project-owned by the backtest engine.

The integrated adapter has not been run with a real Qlib installation or on
real market data. CI uses synthetic frames, stub Qlib APIs and a fake
LightGBM module. Model and momentum continue to share the same pre-test
baseline NAV and BacktestConfig in the explicit walk-forward workflow; no
empirical performance claim is made until an accepted real-data run occurs.

`persist_walk_forward_model_run(...)` writes the final Booster for each fold,
model/benchmark predictions and ledgers, admissions, configs and provenance.
`verify_walk_forward_model_run(...)` checks the artifact inventory and SHA-256
checksums without loading or fitting a model. These persistence APIs have only
been exercised with synthetic fake-Booster artifacts.