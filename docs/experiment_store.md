# Walk-forward experiment artifact store

## Purpose

Persist a completed deterministic momentum walk-forward run as an immutable,
self-describing experiment directory. The store is deliberately a small local
file contract, not a database, scheduler, dashboard, or model-training system.

The store does not call CNEquity, download data, build features/labels, fit
models, or change `training_dataset_unlocked`.

## Artifacts

Each experiment writes one directory:

```text
<root>/<experiment_id>/
  manifest.json
  folds.json
  fold_table.parquet
  oos_daily.parquet
  oos_orders.parquet
  oos_holdings.parquet
```

The manifest binds:
- Explicit walk-forward, strategy and backtest/cost configurations.
- The Git commit that produced the run, runtime and Polars versions, and
  optional random seed.
- Acceptance report identity, universe profile, source receipts and the
  required dataset revision identifiers.
- Per-fold definitions, cutoff/provenance, NAV baseline and fold report.
- SHA-256 hashes, row counts and columns for each Parquet artifact, plus the
  hash and row count for `folds.json`.
- Aggregate fold diagnostics and explicit false values for model training and
  training unlock.

The Parquet ledgers preserve their Polars schema and are directly readable
with `polars.read_parquet`. The manifest is JSON with
`schema_version=1`.

## API

```python
from ashare_quant.backtest import BacktestConfig
from ashare_quant.research import (
    WalkForwardConfig,
    persist_walk_forward_momentum_run,
    run_walk_forward_momentum,
)
from ashare_quant.strategies.momentum import MomentumConfig

walk_forward_config = WalkForwardConfig()
strategy_config = MomentumConfig()
backtest_config = BacktestConfig(top_n=strategy_config.top_n)

run = run_walk_forward_momentum(
    validated_bundle,
    walk_forward_config=walk_forward_config,
    strategy_config=strategy_config,
    backtest_config=backtest_config,
)

saved_path = persist_walk_forward_momentum_run(
    run,
    root="reports/experiments",
    experiment_id="momentum-wf-2026-11-13-001",
    repository_commit="0123456789abcdef0123456789abcdef01234567",
    walk_forward_config=walk_forward_config,
    strategy_config=strategy_config,
    backtest_config=backtest_config,
)
print(saved_path)
```

Pass the effective configurations actually used by the runner, including
defaults resolved by the caller. Pass the code commit used to execute the run
(for a local checkout, capture `git rev-parse HEAD` before running it).
Do not use the example SHA as a real repository revision.

## Safety and immutability rules

- `experiment_id` is a single safe path segment; path separators and traversal
  are rejected.
- The destination is never overwritten. Re-running an experiment requires a
  new ID, preserving the prior evidence.
- Files are written to a temporary sibling directory and the completed
  directory is renamed into place. A failed write cleans up its staging
  directory.
- Folds must share one acceptance identity, universe profile and exact source
  revision map. Required market, factor, universe, trading-status, calendar
  and price-limit revision identifiers must be present.
- Source revisions are structured `(revision_id, contract_fingerprint)` identities,
  not free-form labels. Persist/verify recomputes dependency identities from the
  retained adapter receipts and checks them against the declared revision map;
  the historical-universe identity must alias the receipt-bound daily-bars
  identity. The separate price-limit identity must match the row-level evidence
  provenance. Acceptance receipts for historical universe and HFQ remain
  separately retained and replayable.
- Each fold's common provenance must exactly match the experiment manifest;
  the per-fold walk-forward cutoff is validated separately.
- Every fold must retain its own test-end cutoff and have test-only daily,
  order and holdings ledgers.
- A run that claims model training was performed or training was unlocked is
  rejected.
- Non-finite values and unsupported metadata types are rejected rather than
  silently stringified.

## Validation boundary

Unit tests use synthetic folds and ledgers. A passing CI run validates the
file contract, immutability, metadata binding and Parquet round-trip only. It
does not validate local CNEquity coverage, price-limit evidence, strategy
performance or empirical research quality.

## Integrity verification and fair comparison

Use `verify_walk_forward_experiment(path)` before reading or exporting a
saved experiment. It verifies the manifest schema and config hash, safe
artifact paths, SHA-256 hashes, Parquet row counts/columns, fold-table identity,
per-fold test cutoffs, OOS session coverage, aggregate metrics and training
lock markers. It returns the verified manifest, fold table and OOS Parquet
frames.

Use `compare_walk_forward_experiments(paths)` for a lightweight parameter
comparison:

```python
from ashare_quant.research import (
    compare_walk_forward_experiments,
    verify_walk_forward_experiment,
)

verified = verify_walk_forward_experiment("reports/experiments/momentum-a")
comparison = compare_walk_forward_experiments([
    "reports/experiments/momentum-a",
    "reports/experiments/momentum-b",
])

print(verified.manifest["experiment_id"])
print(comparison.summary)
print(comparison.by_fold)
```

Comparison first verifies every experiment. It then requires identical
acceptance report identity/window, universe profile, source receipts/revisions,
walk-forward config, fold dates and backtest/cost config. Strategy parameters
may differ. The result contains:
- `summary`: one aggregate row per experiment.
- `by_fold`: per-fold return, CAGR, volatility, Sharpe, drawdown and cost
  attribution for side-by-side diagnosis.

The comparison API reports results only. It does not automatically select a
winner or optimize strategy parameters against test windows. Keep test windows
untouched for final out-of-sample evaluation.

This is a local file API for the existing momentum runner. It adds no database,
scheduler, dashboard, model runner or command-line orchestration.
