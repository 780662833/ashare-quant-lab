# Qlib DatasetH and LightGBM integration

The Phase 9 walk-forward runner uses Qlib `DatasetH` as its standardized
research dataset interface and LightGBM as its single cross-sectional return
regressor. Project-owned Polars frames, source receipts, PIT admission,
label maturity and the A-share execution engine remain authoritative.

## Responsibilities

- Polars and project assemblers build features and five-session forward labels.
- `admit_training_dataset(...)` must accept the exact fold frames before they
  enter the Qlib adapter. The acceptance report's
  `training_dataset_unlocked=false` flag is preserved.
- `QlibDatasetBundle` binds a `DatasetH` instance to that admission object and
  feature schema. `DatasetH.prepare(...)` exposes chronological train,
  validation and test segments.
- `fit_qlib_lightgbm_regressor(...)` reads `feature` and `label` from Qlib's
  learn view (`DataHandlerLP.DK_L`) and fits the existing deterministic
  LightGBM regression model.
- `predict_qlib_lightgbm_regressor(...)` reads only the feature group from
  Qlib's inference view (`DataHandlerLP.DK_I`) and returns keyed Polars
  predictions.
- Validation predictions are scored separately against only validation labels
  that have matured strictly before the fold test start. Rank IC, Pearson IC,
  Top-N excess vs the labeled cross-section and Top-Bottom spread are saved as
  validation diagnostics; test-period labels are not passed to this evaluator.
- `weekly_cross_sectional_model_targets(...)` ranks predictions by signal
  date and emits Top-20 equal-weight targets. The project backtest engine
  retains ownership of T+1, fees, price limits, orders, fills, unfilled orders
  and NAV accounting.

## Segment and leakage rules

For each walk-forward fold, the diagnostic pass supplies admitted training
rows as the `train` segment and validation features as a feature-only
`valid` segment. Validation labels are not placed in Qlib's inference frame.
Before the final test pass, the project re-runs admission at the test cutoff
using only train-plus-validation examples whose five-session labels have
matured. Those examples form the final `train` segment; test rows are added
only as feature-only inference rows.

The Qlib bridge rechecks the admission fingerprints, label-key coverage,
maturity cutoff and non-overlapping segment bounds. Admission fingerprints
cover the complete input feature and label frames. Some feature rows may
legitimately have no future label (for example, an unavailable future price
endpoint), and some historical feature vectors may contain null values while
rolling windows warm up. Every admitted label must match a feature; only rows
with a mature label and a complete finite feature vector enter the Qlib learn
segment. Null features are excluded, never imputed. The bundle records the
actual materialized training-row count separately from admission source-row
counts, and the LightGBM fit adapter verifies that count.

Inference feature rows are checked for unique keys, numeric completeness and
their assigned non-train segment. The Qlib index uses a pandas datetime level
and an exchange-qualified instrument level. The project walk-forward planner
remains responsible for label-horizon purge and embargo; Qlib provides the
dataset segment interface and selected-data access, not a replacement split
policy.

## Dependency and validation boundary

Qlib and pandas remain lazy optional research dependencies. The project stays
on its Python 3.13 main development environment; a separate GitHub Actions job
installs Qlib 0.9.7 on Python 3.12 and exercises the real
`DataHandlerLP.from_df()`, `DatasetH.prepare()` and datetime MultiIndex
contracts with synthetic rows. The same smoke test constructs a real
LightGBM Dataset and replaces only `lightgbm.train` with a prediction stub,
so no model optimization or empirical training occurs. This does not change
project dependency pins or the CNEquity version lock.

The base CI continues to use synthetic Polars frames and stub Qlib/pandas
objects, and remains usable without the optional research dependencies. The
separate real-API job does not read the local data lake, call CNEquity, train
a model, or start a backtest. No empirical model result is implied.
