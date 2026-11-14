# Research architecture and engineering contract

**Status:** v1 architecture baseline  
**Last updated:** 2026-11-14  
**Repository:** `780662833/ashare-quant-lab`

This document is the system-level reference for implementation decisions. A
design target is not the same as an implemented capability: where code and
design disagree, record the gap, add tests, and update the relevant document.

## 1. Product objective

Build a small, reproducible A-share quantitative **research** system that can
answer three questions:

1. Does a cross-sectional stock-selection strategy have out-of-sample ranking
   ability?
2. Does its excess return survive transaction costs and orders that cannot
   be filled?
3. Is the result stable across different chronological market regimes?

The goal is not to maximize downloaded data volume or deliver automated
trading. CNEquity is the background market-data engineering dependency; the
research platform and its verifiable experiment loop are the primary
development work.

## 2. Frozen v1 scope

| Area | v1 decision |
|---|---|
| Market | Mainland China A-shares; default research universe is CNEquity's documented SH/SZ profile |
| Strategies | One cross-sectional model strategy and one transparent momentum benchmark |
| Portfolio | Top 20, equal-weight targets, no leverage |
| Rebalance | Weekly, on the final supplied exchange session in each ISO week |
| Signal / execution | Signal after close(t); first possible execution is next eligible session's open |
| Research stack | Python, CNEquity 0.21.0, DuckDB, Polars, Qlib, LightGBM |
| Simulation | Project-owned deterministic backtest engine shared by both strategies |
| Outputs | Daily NAV/returns, holdings, order ledger, costs, benchmark comparison, risk report, later walk-forward folds |
| Training gate | Historical universe, HFQ, strict PIT, and trading-status acceptance must all pass for the same window and data revisions |
| Live trading | Explicitly out of scope |

Also out of scope: LLM-generated trading decisions, ETFs, leverage, portfolio
optimizers, multi-strategy orchestration, and a complex dashboard.

## 3. Component boundaries

```mermaid
flowchart TD
    CE[CNEquity 0.21.0<br/>authoritative facts] --> AD[Adapter + acceptance runner]
    AD --> DB[Canonical research data bundle]
    DB --> ST[Strategy interface]
    ST --> MOM[Weekly momentum benchmark]
    ST --> ML[Future LightGBM / Qlib model]
    MOM --> BT[Shared A-share backtest engine]
    ML --> BT
    BT --> RP[Backtest result]
    RP --> REP[Performance and execution report]
    GA[Four-gate data acceptance] -->|required before training data| ML
```

### CNEquity

- The only authoritative source for historical market, adjustment, universe,
  trading-status, and point-in-time fundamental facts.
- Pinned to `cnequity==0.21.0` until a separately reviewed upgrade.
- Provider-specific calls belong behind the CNEquity adapter and the explicit
  acceptance runner. Strategy and backtest code must not query provider
  internals.
- Downloads may run gradually in the background. An incomplete historical
  lake does not block deterministic engine, report, strategy, or synthetic
  integration work.
- Never create a second market-data source of truth.

### DuckDB and Polars

- DuckDB is the local analytical/query layer; Polars is used for typed,
  deterministic transformations and DataFrame contracts.
- They transform and query CNEquity facts; they do not replace CNEquity as
  the authoritative provider.
- Missing, duplicated, or contradictory facts remain explicit. Do not silently
  forward-fill unexplained gaps.

### Strategy layer

- A strategy consumes canonical research inputs and emits target weights keyed
  by `signal_date` and `instrument`.
- It does not place orders, query the provider, calculate executable prices,
  or change cash/positions.
- The momentum benchmark is deterministic and does not require model training.
- The future model strategy must use the same input and target contracts.

### Backtest engine

- One engine owns execution, cash, positions, order states, costs, and NAV.
- Momentum and model targets pass through the same engine and configuration.
- Raw OHLC is used for execution. HFQ adjusted prices are research-only.
- No-fill and partially filled orders remain in the ledger; the system must
  not replace unfilled selections with securities that later performed better.

### Report layer

- Consumes `BacktestResult`; it does not read market data or modify accounting.
- Strategy and benchmark must be aligned on their common observed window.
- Positive excess return is descriptive evidence, not proof of significance
  or future profitability.

### Qlib and LightGBM

- Qlib and pandas are optional research dependencies; base synthetic backtest
  and momentum workflows do not require Qlib.
- Phase 9 uses Qlib `DataHandlerLP.from_df` / `DatasetH` as the primary
  research dataset interface. It accepts only admission-bound Polars
  train/label frames, exposes chronological segments and keeps validation/test
  rows feature-only for inference.
- LightGBM is the single cross-sectional regression model. It reads Qlib's
  learn view for fitting and infer view for keyed predictions; project-owned
  Top-20 equal-weight target generation remains downstream.
- All fit boundaries recheck the execution-aligned label contract:
  the direct LightGBM adapter and Qlib panel conversion require canonical
  signal, entry and maturity dates in strict order before any optimizer call.
- Walk-forward purge calculations distinguish the one-session next-open
  entry lag from the open-to-open holding horizon; maturity dates include both
  before adding the configured embargo. Validation predictions are evaluated
  against validation labels only after label maturity strictly before test
  start. Cross-fold stability summaries
  are descriptive by default; any acceptance policy must be explicit,
  versioned and fingerprinted. No test labels feed policy decisions.
- Project-owned Polars features/labels, CNEquity receipts, PIT availability,
  historical universe, trading status, walk-forward splits and admission
  remain authoritative. Weekly feature construction requires exact HFQ
  adjustment evidence for every adjusted-close row; strict PIT facts must be
  finite and duplicate availability keys are rejected. PIT values select the
  latest report period known at the signal timestamp and then the latest
  revision within that period. Zero-volume rolling windows produce a null
  volume-ratio feature, never a NaN 0/0 result. All market-derived model
  features must be non-null and finite; strict `pit_*` fundamentals may
  remain null when no fact was knowable at the signal close. The adapters
  transport only these true PIT nulls as NaN to LightGBM's native missing-value
  handling (`use_missing=true`, `zero_as_missing=false`). Non-null NaN/Inf
  source values and incomplete market features remain ineligible; no value
  is zero-filled or forward-filled. Qlib is a dataset/workflow adapter, not a
  data source of truth or an admission bypass.
- A-share execution, costs, price limits, order fills and portfolio accounting
  remain project-owned and must not be delegated to generic defaults.
- Qlib and pandas stay optional/lazy in the Python 3.13 development
  environment. A separate Python 3.12 Qlib 0.9.7 workflow exercises the real
  Qlib data APIs and LightGBM Dataset constructor with synthetic frames; the
  LightGBM optimizer remains stubbed. No dependency or CNEquity version lock
  is changed.
- Base synthetic CI still stubs optional Qlib and LightGBM APIs. Real API
  smoke coverage does not constitute real-data training, a backtest or an
  empirical performance claim.

## 4. Canonical data and time contract

### Keys and dates

- Security key: exchange-qualified identifier such as `600000.SH`.
- Market date: exchange-local trading date.
- Primary panel key: `(instrument, date)`; duplicates are a hard error.
- Signal date: session whose close is available to the strategy.
- Execution date: next eligible exchange session, never the same close.
- Fundamentals carry both report period and publication/availability time.
  Report period is not a substitute for when a fact became knowable.
- Supervised return labels begin at the first executable open after signal close
  and end at the first executable open after the next weekly signal. Their
  explicit entry and maturity dates follow the supplied exchange calendar,
  including holiday-shortened weeks; they do not include the untradable
  signal-close-to-entry-open return.

The future research bundle must use the official exchange calendar. Do not
infer calendar sessions from the dates on which one selected stock happened
to trade.

### Price roles

| Price field | Allowed use |
|---|---|
| Raw `open/high/low/close` | Execution simulation and explicitly defined marking |
| HFQ `adj_*` | Historical return and research feature calculations |
| Raw close as a momentum feature | Not allowed |
| Adjusted price as a fill price | Not allowed |

HFQ factor coverage and economic adjustment correctness are separate checks.
An `adj_is_exact` flag or successful strict-adjust query alone is not a
third-party accuracy certification.

### Universe and availability

- Universe membership is point-in-time on every signal date.
- The walk-forward model runner revalidates caller-supplied feature and label
  keys against the bundle's same-date membership snapshot; data prepared
  outside the canonical feature builder cannot introduce non-members.
- Historical listing/delisting, ST/risk-warning, exchange, and board history
  must not be reconstructed from today's listed names.
- Delisted securities remain in historical research where they were eligible.
- Fundamental facts use strict as-of availability. Revisions must not rewrite
  what was knowable at an earlier decision timestamp.
- For each instrument/item, select the latest report period known at the
  signal timestamp, then the latest available revision within that report
  period. A later restatement of an older period cannot displace a newer
  period already published. Report periods must be canonical dates and cannot
  be later than their publication date.
- Empty strict PIT output is a coverage failure, not permission to use
  best-effort mode.

### Separate signal data from execution evidence

The canonical research bundle must carry separate inputs:

1. Adjusted price panel for signals/features.
2. Historical point-in-time membership by signal date.
3. Raw OHLC panel for execution and marking.
4. Official exchange trading calendar.
5. Historical suspension, risk-warning, and trading-status evidence.
6. Price-limit values and limit-lock evidence for execution sessions.
7. Source receipts, data revisions, and applicable acceptance-report identity.

One type of price or one source field must not be silently substituted for
another.

## 5. Four non-bypassable data acceptance gates

All four gates are required before any model-training dataset can be built.
They must pass for the **same requested window, universe profile, and source
data revisions**.

| Gate | Required evidence | Fail-closed behavior |
|---|---|---|
| Historical universe | Historical membership, listing/delisting, ST evidence, interior session coverage, delisted-name/survivorship checks | Missing scope or receipts = FAIL |
| HFQ adjustment | Strict HFQ output, factor coverage, revision-bound historical factor/corporate-action diagnostics | Missing factors, unassessed diagnostics, unresolved contradictions = FAIL |
| PIT fundamentals | Non-empty `pit_mode="strict"` result, exact PIT evidence, `pit_quality="strict"`, requested symbol/item coverage, announcement no later than as-of | Empty, reconstructed, incomplete, or future-published fact = FAIL |
| Trading status | Status evidence and known risk-warning state for observed positive-volume SH/SZ bars, with contradictions surfaced | Missing or contradictory evidence = FAIL |

Important scope notes:

- Current trading-status acceptance is scoped to observed positive-volume SH/SZ
  bars. It is **not** a complete instrument-by-calendar matrix proving every
  suspension/non-trading date.
- Historical-universe and HFQ diagnostics are bounded by evidence from the
  pinned CNEquity release and the exact query receipt.
- Four-gate PASS is necessary but not sufficient to unlock training.
  `training_dataset_unlocked` remains false until a future builder verifies
  report identity, window, profile, and revisions and stores them with the
  dataset.
- A pure price-momentum benchmark does not consume fundamental PIT features;
  it still requires valid historical membership, adjusted-price evidence,
  exchange calendar, and execution-state evidence. This does not authorize
  model training.

See [data_contract.md](data_contract.md) for the operational contract.

## 6. Trading and accounting contract

The current engine implements a deterministic first slice:

- Close(t) signal, next supplied row-date open execution.
- Explicit suspension, limit-up lock, and limit-down lock flags are mandatory.
- Supplied price limits must be positive; an open inconsistent with a supplied
  limit is rejected.
- Sells are attempted before buys.
- Buy quantities are rounded down to 100-share lots by default.
- No leverage or negative cash; unaffordable buys are reduced by whole lots.
- Commission/minimum commission, sell stamp duty, transfer fee, and slippage
  are reflected in filled prices/cash once; slippage is not double deducted.
- Missing bars and blocked orders remain visible as no-fill records.
- Existing positions remain when a sell cannot execute.
- Daily NAV is cash plus marked positions.

This is **not yet a full historical exchange simulator**. The accounting
ledger enforces the T+1 invariant that shares acquired in a session cannot be
sold in that same session; it does not model exchange settlement cash
availability or every broker-specific settlement detail. Remaining gaps
include a validated historical price-limit rule/evidence provider for
board- and date-specific limits and IPO no-limit windows, corporate-action
share/cash adjustments, historically authoritative odd-lot liquidation
rules, and a full security-by-calendar status matrix. CNEquity 0.21.0 native
trading_status does not supply the limit prices or lock flags. Do not describe
results as production-grade A-share simulation until the remaining execution
evidence and market-rule gaps are implemented and tested.

Configured costs are experiment assumptions, not a statement of current
broker/exchange schedules. Record the exact configuration for every run.

See [backtest_rules.md](backtest_rules.md) and
[backtest_report.md](backtest_report.md).

## 7. Momentum benchmark contract

The first benchmark is a weekly, cross-sectional, top-20 equal-weight
momentum portfolio:

`score(t) = HFQ_close(t - 21 sessions) / HFQ_close(t - 126 sessions) - 1`

- Rank descending, with deterministic symbol tie-breaking.
- Use membership as-of the signal date.
- Require exact endpoint prices; exclude a candidate missing either endpoint.
- Do not forward-fill missing prices or substitute raw close.
- Emit target weights only; execute through the shared engine next session.
- With fewer than 20 valid names, divide target weight equally among those
  names. An empty valid set produces no targets.

See [momentum_benchmark.md](momentum_benchmark.md).

## 8. Experiment comparability and reporting

Model and benchmark runs must use identical:

- Historical universe and date window.
- Exchange calendar and weekly signal dates.
- Engine version and execution configuration.
- Raw OHLC and status/price-limit evidence.
- Cost assumptions and initial capital.
- Portfolio size, weighting, and no-leverage rule.
- NAV marking and report calculations.

Every run should retain strategy parameters, repository commit, package
versions, data receipts/revisions, requested window, universe profile,
execution/cost configuration, and random seed where applicable.

Report total return, CAGR, annualized volatility, Sharpe, maximum drawdown,
gross turnover, fill ratio, no-fill counts, itemized fees plus slippage cost,
and benchmark excess return over the same common window. Walk-forward work must report
fold-level and regime-level results, not only pooled headline metrics.

## 9. Development discipline

- Synthetic fixtures are the default for deterministic engine/strategy tests.
- CI passing synthetic tests does not certify the local CNEquity lake.
- Real-lake acceptance runs against the user's configured local data root and
  produces a separate evidence report.
- Data acquisition is not a blocker for pure research-engine development, but
  missing data cannot be bypassed for empirical claims or model training.
- Do not repeatedly ask the user to run large downloads for ordinary code
  changes.
- Do not claim local commands, tests, acceptance, or backtests ran unless the
  corresponding environment actually executed them.

## 10. Current maturity snapshot

As of 2026-11-14:

| Capability | State |
|---|---|
| CNEquity 0.21 adapter and four-gate acceptance | Implemented; real-lake gates require local evidence |
| Deterministic first-slice backtest engine | Implemented; exchange-simulation gaps remain |
| Performance/execution report | Merged; PR #12 adds itemized commission, transfer fee, stamp duty and separately attributed slippage |
| Weekly momentum score/targets | Merged in PR #9; canonical bundle orchestration merged in PR #10; direct CNEquity-to-limit-evidence input remains pending |
| Unified research data bundle | Merged in PR #10; core builder, momentum orchestration and native CNEquity status normalization have synthetic coverage; direct source assembly, audited historical price-limit evidence and local-lake validation remain pending |
| Walk-forward fold planner | Merged in PR #11; expanding/rolling temporal partitions, label-maturity purge, embargo and non-overlapping test windows; no fitting |
| Walk-forward momentum runner | Merged in PR #13; cutoff-isolated deterministic momentum replay and test-only fold ledgers; no model training |
| Experiment artifact store and comparison | Merged in PR #15 and #16; immutable manifest, source/config provenance, SHA-256 Parquet ledgers, artifact verification and fair momentum-parameter comparison; 97 synthetic unit tests pass |
| Cross-sectional feature/label + strict admission | Implemented; empirical use remains gated by local data acceptance |
| Qlib DatasetH → LightGBM → Top-20 walk-forward pipeline | Merged in PR #67; 286 synthetic CI tests passed |
| Validation-only ranking diagnostics | Merged in PR #70; per-week Rank IC, Pearson IC, Top-N excess and Top-Bottom spread, maturity checked before test start; no test labels or automated tuning |
| Cross-fold validation stability policy | Merged in PR #71; descriptive weekly IC distribution/ICIR, positive-week and positive-fold ratios, coverage and Top-N excess stability; minimum fold/date/valid-IC/cross-section/coverage/ranking thresholds are explicit, versioned and fingerprinted. Base CI 305 passed/2 skipped; real Qlib integration 2 passed |
| Real Qlib 0.9.7 / Python 3.12 compatibility | Verified in PR #68 and #69: Python 3.12.16, Qlib 0.9.7, Pandas 2.3.3, LightGBM 4.7.0; DatasetH API and full multi-fold runner both passed synthetic integration tests; optimizer stubbed, no model fit |
| Live trading | Out of scope |
