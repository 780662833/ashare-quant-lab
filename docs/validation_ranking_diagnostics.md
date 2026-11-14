# Validation-only cross-sectional ranking diagnostics

These metrics answer whether the diagnostic model orders validation stocks in a
useful way before the fold's final train-plus-validation refit. They complement
the portfolio backtest; they do not replace net-of-cost test-window results.

## Data boundary

1. Fit the diagnostic model using only the fold's admitted train rows.
2. Predict the validation segment through Qlib's feature-only inference view.
3. Select labels anchored on validation signal dates only.
4. Reject the diagnostic input if any supplied label end date is greater than
   or equal to the fold test start. Validation labels must have matured before
   the final refit/test cutoff.
5. Join predictions and labels by exact (signal_date, instrument) keys.
   Missing label endpoints lower coverage and do not become synthetic returns.

The module rejects labels whose signal dates fall outside the validation
window. It never accepts test-period labels. Metrics are computed after
predictions have been generated and do not feed model parameters, early
stopping, feature selection, or hyperparameter search.

## Per-signal-date metrics

- **Spearman rank IC:** Pearson correlation of average-tie ranks for predicted
  returns and realized forward returns.
- **Pearson IC:** linear correlation of predicted and realized returns.
- **Top-N mean forward return:** average labeled forward return among the
  highest-scored top_n predictions, using the same descending score and
  deterministic instrument tie-break used by target generation.
- **Top-N excess vs universe:** Top-N mean return minus the full matched
  validation cross-section's mean return.
- **Top-Bottom spread:** difference between equal-sized highest-score and
  lowest-score buckets. Bucket size is min(top_n, floor(sample_count / 2)),
  which prevents overlap in intentionally small synthetic universes.
- **Top-N positive ratio:** fraction of Top-N labeled returns greater than zero.

Fold summaries are equal-weight means across signal dates, not pooled row
correlations. Rank-IC summaries also report median IC and the proportion of
evaluated signal dates with positive IC. Rows with fewer than two labeled
stocks can contribute Top-N return but cannot contribute a defined IC or
Top-Bottom spread.

## Persistence

Each model-run fold stores:

- validation_predictions.parquet: model output before labels are joined.
- validation_diagnostics.parquet: per-signal-date metrics.
- fold.json: the aggregate validation report, including label coverage and
  the exact maturity cutoff.

The root aggregate report and fold_table.parquet include validation ranking
summaries alongside, but separately from, test-period NAV and benchmark
results. A missing or insufficient validation cross-section is recorded as a
diagnostic status; it is not silently converted into zero IC.

These diagnostics are descriptive validation evidence, not a license to tune
against validation repeatedly. Any later model/config selection protocol must
be declared separately and keep the walk-forward test windows untouched.
