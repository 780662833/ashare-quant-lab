# Walk-forward split plan (Phase 6)

## Purpose and boundary

This phase builds chronological fold boundaries only. It does not create
features or labels, fit a model, tune hyperparameters, access CNEquity, or
override model-training admission. A fold plan is not evidence that a model
was trained or that a backtest is statistically valid.

Both the future model strategy and the momentum benchmark must use the same
fold dates and test windows when comparing results.

## Fold layout

For each fold:

1. **Train**: expanding history by default; optional fixed-length rolling window.
2. **Purge + embargo**: wait for the training label horizon to mature, then
   leave the configured embargo sessions.
3. **Validation**: a chronological period used for model selection only after
   its labels have matured.
4. **Purge + embargo**: wait for validation label outcomes to mature, plus the
   embargo sessions.
5. **Test**: strictly later out-of-sample period.

For a label horizon of H sessions and embargo E sessions, the gap between
partition boundaries is H + E exchange sessions. The label maturity date is
recorded for audit. Train labels must mature before validation starts;
validation labels must mature before test starts.

## Configuration

| Parameter | Default | Meaning |
|---|---:|---|
| `min_train_sessions` | 504 | Minimum initial training history |
| `validation_sessions` | 63 | Validation period length |
| `test_sessions` | 63 | Test period length |
| `step_sessions` | null (uses test length) | Advance of the next fold's train boundary |
| `label_horizon_sessions` | 5 | Future sessions needed to realize a label |
| `embargo_sessions` | 1 | Extra separation after label maturity |
| `expanding_train` | true | Grow training history; false uses a rolling fixed-length window |

The supplied date list is the authoritative exchange calendar and must already
be strictly chronological and unique. The splitter does not infer weekends,
holidays, or trading sessions from bars.

Test windows cannot overlap. The default step equals test length, producing
adjacent test windows where sufficient history exists. Explicit steps shorter
than test length are rejected.

## Output

Each `WalkForwardFold` includes train/validation/test date boundaries,
train and validation label-maturity dates, partition session counts, horizon,
embargo, and expanding/rolling mode. Folds can be serialized with
`to_dict()` for an experiment manifest.

## Training and anti-bias safeguards

- No random or shuffled splits.
- No validation/test observation is moved into an earlier partition.
- No training label is allowed to remain unresolved at validation start.
- No validation label is allowed to remain unresolved at test start.
- The same folds must be applied to model and benchmark comparisons.
- A split plan does not create features/labels and does not unlock training.
- The four CNEquity gates (historical universe, HFQ, strict PIT, trading
  status) remain mandatory for model-training dataset construction.
- Real execution/backtest quality still depends on the documented T+1,
  historical price-limit, IPO, corporate-action and status-evidence gaps.

## Current limitation

The Phase 8 momentum runner now applies these folds to the fixed weekly
momentum benchmark. It replays each fold only through its test-end date and
returns test-only ledgers plus fold metrics. It does not fit or evaluate a
LightGBM model. A future model runner must use the same fold windows after the
four-gate training admission has been independently enforced. See
[walk_forward_runner.md](walk_forward_runner.md).
