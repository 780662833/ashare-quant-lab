# Qlib DatasetH bridge

The Phase 9 model path keeps the project-owned Polars feature/label contracts,
PIT assembly, source receipts, label-maturity checks and fail-closed admission
as the authoritative boundary. Qlib is an optional dataset adapter, not a
second data source or a replacement for those controls.

## Conversion contract

- Accept only a `TrainingDatasetAdmission` whose `admitted` flag is true.
- Recompute and compare the complete feature and label frame fingerprints.
- Require all admitted rows and exact key alignment; reject null/non-finite values.
- Require every forward label to mature strictly before the recorded training
  cutoff.
- Convert keys to Qlib's two-level `(datetime, instrument)` index.
- Convert columns to two groups: `feature` and `label`.
- Create `DataHandlerLP.from_df` and `DatasetH` lazily. Qlib and pandas remain
  optional research dependencies and are not imported at package import time.
- Reject overlapping dataset segments. The caller remains responsible for
  passing the chronological train/validation/test segments created by the
  project's walk-forward planner.

Qlib does not own A-share membership, CNEquity receipts, PIT availability,
execution prices, price limits, transaction costs, fills or portfolio
accounting. The native admission and backtest contracts remain mandatory.

## Current boundary

CI tests use synthetic Polars frames and stub Qlib/pandas modules. They verify
the conversion interface without installing Qlib, reading a local data lake,
calling CNEquity, or fitting a model. No empirical model result is implied.
