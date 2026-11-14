# Cross-fold validation ranking policy

## Purpose

Phase 9.10 emits per-signal-date validation ranking diagnostics. Phase 9.11
summarizes them across chronological folds and optionally applies an explicit,
versioned acceptance policy. This makes "metrics were calculated" distinct
from "the validation evidence met a declared research bar".

The policy evaluates validation outputs only. It is not a substitute for the
four-gate source-data admission, and it does not certify OOS profitability.

## No implicit thresholds

There is no universal IC/ICIR cutoff suitable for every universe, rebalance
horizon, label definition and sample size. The runner therefore has no hidden
default thresholds. A caller must explicitly construct
`ValidationRankingAcceptancePolicy` and provide:

- `policy_id`: stable, human-readable identifier for the frozen rule set.
- `minimum_fold_count` and `minimum_signal_date_count`: minimum validation
  evidence volume.
- `minimum_label_coverage_rate`: minimum pooled count of matched mature
  labels divided by prediction rows.
- `minimum_mean_spearman_rank_ic`: minimum mean weekly cross-sectional rank IC.
- `minimum_positive_spearman_rank_ic_ratio`: minimum fraction of defined
  weekly Rank IC observations above zero.
- `minimum_positive_top_n_excess_fold_ratio`: minimum fraction of folds with
  positive mean Top-N excess against that fold's labeled validation universe.
- Optional `minimum_positive_rank_ic_fold_ratio`,
  `minimum_rank_icir` and
  `minimum_mean_top_n_excess_forward_return` thresholds.

The supplied policy is serialized into the aggregate report and each fold's
provenance. A SHA-256 fingerprint identifies its exact values. Changing a
threshold or policy identifier changes the fingerprint. Policy values are
research assumptions and must be fixed before examining the corresponding
validation results; this implementation does not pick or optimize them.

## Cross-fold statistics

The summary reports:

- Weekly Rank IC mean, median, sample standard deviation, positive ratio and
  descriptive ICIR (mean divided by sample standard deviation).
- Fold-level mean Rank IC and the fraction of folds with positive mean IC.
- Pooled label coverage, total prediction and matched-label counts.
- Mean weekly Top-N excess and the fraction of folds with positive mean Top-N
  excess.
- Mean weekly Top-Bottom spread and its positive-fold ratio.
- Per-fold sample counts, validation period and exact label-maturity cutoff.

ICIR is not annualized. Weekly IC observations are not assumed independent;
no t-statistic, p-value or confidence interval is manufactured. Missing IC
values are excluded from IC statistics and counted separately. Fold Top-N
excess consistency uses folds with a defined value as its denominator.

## Decisions

- `NOT_ASSESSED`: no explicit policy was supplied; summaries are descriptive.
- `ACCEPTED`: all explicit policy thresholds passed on validation evidence.
- `REJECTED`: evidence volume is sufficient, but one or more ranking
  thresholds failed.
- `INSUFFICIENT_EVIDENCE`: minimum fold count, signal-date count or label
  coverage failed.

An `ACCEPTED` decision is only validation-policy acceptance. It does not mean
that test returns are profitable, that transaction costs are survived, or
that results generalize to another time period. The untouched walk-forward
test windows remain the final OOS evaluation and are not read by this policy
module.

The policy module must not trigger a refit, alter model parameters, change
feature selection, select a model based on test returns, or unlock training.
