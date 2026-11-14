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
- Portfolio: top 20 predicted names, equal weight, no leverage.
- First learner: LightGBM regression, trained only on chronological folds.
- First label: exact HFQ close-to-close forward return over 5 exchange sessions.
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

For signal date t and horizon h=5:

forward_return = HFQ_close(t+h) / HFQ_close(t) - 1

Both endpoints must exist exactly. The label row carries label_end_date.
Rows without a future endpoint are omitted, not filled. Membership is frozen
as-of t; the future universe is not used to decide which historical names
receive labels. Label maturity must be checked against fold cutoffs before any
training or validation use.

## PIT fundamental handling

Input rows must carry instrument, item, report_period, available_at, value,
pit_quality, and pit_is_exact. Only pit_quality='strict' and
pit_is_exact=true facts are eligible. available_at must be timezone-aware.
The builder compares it with the signal-date close cutoff (16:00
Asia/Shanghai), then selects the latest known report for each instrument/item.
A report period is never treated as its publication timestamp.

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

## Model adapter and portfolio targets

`fit_lightgbm_regressor(...)` uses the optional `research` extra and refuses to
fit unless it receives a successful admission object whose exact feature and
label frame fingerprints still match. It drops incomplete feature rows,
requires a configured minimum sample count, and uses deterministic single-
thread LightGBM settings. Model artifacts contain a feature schema, training
cutoff, source fingerprint and SHA-256 checksum sidecar; load verifies the
checksum before constructing a Booster.

`weekly_cross_sectional_model_targets(...)` ranks each signal-date cross-
section by predicted forward return and delegates Top-N equal-weight target
construction to the shared engine. It emits weights only; all execution and
accounting remain in the backtest engine.

The adapter has not been invoked to fit a model. Remaining Phase 9 work is a
walk-forward model runner that trains only within each fold's admitted training
window, predicts validation/test rows without refitting on them, persists model
and prediction provenance, and compares the model with momentum on identical
folds, costs and execution rules.