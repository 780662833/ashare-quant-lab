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

The Qlib bridge rechecks the admission fingerprints, exact row/key alignment,
finite training values, maturity cutoff and non-overlapping segment bounds.
Inference feature rows are checked for unique keys, numeric completeness and
their assigned non-train segment. The project walk-forward planner remains
responsible for label-horizon purge and embargo; Qlib provides the dataset
segment interface and selected-data access, not a replacement split policy.

## Dependency and validation boundary

Qlib and pandas remain lazy optional research dependencies. The project stays
on its Python 3.13 main development environment; real Qlib compatibility is
to be verified later in a separate Python 3.12 environment. This integration
does not change dependency pins or the CNEquity version lock.

CI uses synthetic Polars frames, stub Qlib/pandas objects and a fake
LightGBM module. It exercises the data bridge, fit/predict contract and
walk-forward wiring without installing or initializing a real Qlib instance,
reading the local data lake, calling CNEquity, training on real data or
starting a backtest. No empirical model result is implied.
