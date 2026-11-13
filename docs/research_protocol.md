# Research protocol (v1)

## Questions

1. Does the model rank stocks with genuine out-of-sample predictive power?
2. Does excess return survive realistic costs and unfilled-order constraints?
3. Does performance persist across distinct chronological market regimes?

## Frozen experiment design

- One weekly LightGBM cross-sectional model strategy, top 20.
- One transparent weekly momentum benchmark, top 20.
- Momentum baseline: 126-session HFQ lookback, skipping the latest 21 sessions.
- Weekly rebalance on the final supplied exchange session in each ISO week.
- Signal after close(t), execution no earlier than the next eligible session open.
- Equal-weight targets; no leverage.
- Model and benchmark use the same historical universe, calendar, engine,
  execution evidence, costs, and report window.
- Walk-forward folds are chronological; training ends before validation/test
  begins. Random time-series splits are prohibited.

## Required outputs

- Daily NAV/returns and holdings snapshots.
- Order/trade ledger including rejected, no-fill, and partially filled orders.
- Benchmark-relative return, annualized return/volatility, Sharpe, max drawdown,
  gross turnover, fill ratio, exposure, and aggregate costs.
- Fold-level and regime-level metrics, not aggregate metrics alone.
- Reproducibility metadata: source revisions/receipts, universe profile,
  strategy parameters, repository commit, software versions, execution/cost
  configuration, requested dates, and random seed where applicable.
- Persist completed walk-forward runs with immutable manifests and checksummed
  Parquet NAV, order and holdings ledgers; see [experiment_store.md](experiment_store.md).

## Anti-bias rules

- Never use current constituents as the historical universe.
- Never use revised fundamentals before actual publication/availability time.
- Never use a feature later than the decision timestamp.
- Never execute at a price unavailable after the decision.
- Never use adjusted prices as executable fills; never use raw close as a
  substitute for HFQ research prices.
- Never forward-fill missing history without a documented, tested policy.
- Never silently replace unfilled selections with better-performing securities.
- Never combine different universe/window/revisions between model and benchmark.
- Keep raw OHLC execution evidence and HFQ research inputs explicitly distinct.

## Training admission

Model training is blocked until all four CNEquity data gates pass for the same
research window, versioned universe profile, and data revisions:

1. Historical universe and delisted-security survivorship evidence.
2. HFQ factor coverage plus revision-bound historical diagnostics.
3. Strict PIT fundamentals: exact quality, strict marker, complete requested
   symbol/item coverage, and announcement time no later than as-of.
4. Historical trading-status evidence with known risk-warning state and no
   contradictions within the accepted scope.

A PASS report is necessary but does not itself create or unlock a training
dataset. The future builder must verify the exact report identity and preserve
its receipts/revisions with the dataset. See [data_contract.md](data_contract.md)
and [architecture.md](architecture.md).

## Walk-forward evaluation

Fold boundaries are generated from the official exchange calendar by
[walk_forward.py](../src/ashare_quant/research/walk_forward.py) using expanding
or fixed-length rolling training windows. The train-to-validation and
validation-to-test gaps purge the configured label horizon and add an embargo.
Out-of-sample test windows must not overlap. See [walk_forward.md](walk_forward.md).
Phase 8 adds a deterministic momentum benchmark runner over those test
windows. For each fold it replays the fixed momentum rule only through that
fold's test-end date, carries the rule's portfolio state into the test period,
and emits test-only ledgers and fold metrics. It does not fit a model. A future
model runner must use the same fold definitions and test windows after the
four-gate training admission has been independently enforced.

## Current implementation boundaries

The first backtest engine is deterministic but is not a full A-share exchange
simulator. T+1 enforcement, full historical board/date price-limit regimes,
IPO no-limit periods, corporate actions, settlement/odd-lot details, and a
complete instrument-by-calendar trading-status matrix remain explicit gaps.
Do not label results research-grade until relevant gaps are implemented,
tested, and covered by source evidence.
