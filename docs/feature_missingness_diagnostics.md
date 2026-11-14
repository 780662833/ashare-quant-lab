# Validation-only PIT feature missingness diagnostics

## Purpose and boundary

Phase 9.21 records whether native LightGBM missing-value handling preserves
useful cross-sectional coverage or creates different score/return behavior for
stocks with unavailable point-in-time fundamentals. The diagnostic is
post-prediction and validation-only. It does not change feature eligibility,
fit a model, select parameters, or consume test-period labels.

The walk-forward runner evaluates the validation predictions against the
validation feature frame and labels that have matured strictly before the
fold's test start. It never uses test labels for these reports.

## Per-signal-date measurements

For each strict PIT feature (currently pit_roe and pit_net_profit) and one
combined any-PIT-missing group, the diagnostic records:

- PIT-universe rows and the count/rate with that fact genuinely null.
- Rows admitted by the shared training/inference feature eligibility rule.
- Prediction rows and PIT-missing count/rate within that eligible set.
- Top-N selection count and missing count/rate, ordered by descending model
  score and ascending instrument for ties.
- Missing-versus-observed group prediction counts and mean predicted returns.
- Matured-label counts, label coverage, mean forward returns and positive-return
  ratios for both groups.

Universe missingness and prediction missingness have different denominators:
the former includes every feature row in the signal-date PIT membership frame,
including rows excluded for missing market features; the latter includes only
rows eligible for model inference. This makes the exclusion caused by market
history distinguishable from the PIT fundamental missingness retained by
LightGBM.

Predictions must exactly cover the rows admitted by the shared feature
eligibility rule. The diagnostic rejects duplicate keys, non-finite feature
sources, prediction mismatches, and labels that are not finite or mature
strictly before the fold test cutoff.

## Group comparison and interpretation

The any-PIT-missing group is a stock with at least one null strict PIT feature
on that signal date. The per-feature groups are calculated independently; a
stock can therefore be missing ROE but have a non-missing net-profit value.

Score comparisons use all validation predictions in each group. Forward-return
comparisons use only group members with a supplied, matured validation label.
Label coverage is reported separately for missing and observed groups so
different future-label availability is not hidden by comparing means alone.

The aggregate report contains pooled row-weighted coverage and group metrics,
plus mean daily missing rates. Per-signal-date records are persisted as
validation_feature_missingness.parquet; the report and validation maturity
cutoff are recorded in fold.json and the root aggregate report.

These measurements are descriptive evidence, not proof that missingness is
random or harmless. They must not be used to tune a model repeatedly against
the same validation windows. Final strategy quality remains determined by
untouched test windows and the shared cost/fill-aware backtest.

## Deferred metadata dimensions

The validated ResearchDataBundle currently carries point-in-time membership
keys (signal date and instrument), market/execution data, and receipt-backed
source provenance. It does not yet carry a receipt-backed, time-valid
market-cap, listing-date or industry dimension table. Phase 9.21 therefore
does not infer size from price/turnover, listing age from the first locally
observed bar, or industry from instrument codes.

Market-cap, listing-age and industry comparisons require an explicit,
historical point-in-time metadata source, receipt/revision pinning, coverage
audits, and synthetic tests for effective-date behavior before they can be
added. This limitation is emitted in each report rather than silently
substituting proxies.
