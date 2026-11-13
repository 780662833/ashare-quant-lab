# Phase 10.1 — Research QA architecture and acceptance criteria

**Status:** P10.1–P10.7 code/report architecture merged (PR #17–20, #37–39); target-fill execution policy remains UNSUPPORTED, and empirical QA remains blocked pending real-data acceptance  
**Date:** 2026-11-13  
**Scope:** Research QA over verified Phase 8 artifacts; reuse the existing runner/store, with no new backtest engine, model training, data backfill, or live-trading capability.

## 1. Purpose and non-goals

Phase 10 adds a research-quality layer around the existing Phase 8 experiment artifacts. It must answer whether reported performance is robust to chronological periods, market states, parameter perturbations, transaction costs, and execution shortfalls.

It is an analysis/validation layer, not a second backtest engine.

Frozen constraints remain in force:

- Historical bulk backfill remains paused.
- Historical universe, HFQ, PIT fundamentals, and trading status remain four separately reported acceptance gates.
- LightGBM training and model-training dataset construction remain blocked unless all four gates PASS for the exact same requested window, universe profile, and source revisions.
- No live trading, LLM decisions, ETFs, leverage, portfolio optimizers, multi-strategy orchestration, or complex dashboard.
- The frozen portfolio contract remains weekly rebalance, top 20 equal weight, no leverage.
- Synthetic tests validate software contracts only; they do not establish empirical strategy validity.

## 2. Existing Phase 8 interfaces to reuse

Phase 8 already provides the execution and evidence lifecycle:

| Existing interface | Phase 10 reuse |
|---|---|
| `run_walk_forward_momentum(bundle, walk_forward_config, strategy_config, backtest_config)` | Produce deterministic benchmark fold results using the existing shared engine and cutoff-isolated bundle prefixes. |
| `persist_walk_forward_momentum_run(...)` | Persist every pre-registered scenario as a new immutable experiment. Never overwrite an existing experiment ID. |
| `verify_walk_forward_experiment(path)` | Verify hashes, manifest/config integrity, fold cutoffs, OOS ledgers, row counts and training-lock markers before QA reads an artifact. |
| `compare_walk_forward_experiments(paths)` | Compare parameter variants only when acceptance identity, provenance/revisions, fold dates, walk-forward config and backtest/cost config match. |
| `WalkForwardConfig` / `build_walk_forward_folds(...)` | Reuse official-calendar chronological folds, label-maturity purge, embargo and non-overlapping test windows. |
| `BacktestResult` reports and OOS daily/order/holding Parquet ledgers | Recalculate QA aggregates and slice verified evidence without changing accounting or fabricating fills. |

The existing comparison API deliberately rejects cost-config differences. Cost-stress scenarios therefore remain separate immutable experiments and are compared by a Phase 10 QA aggregation layer only after each artifact has independently passed verification and provenance alignment. Do not weaken the Phase 8 comparison contract to make unlike runs comparable.

Phase 8 currently persists `walk_forward_momentum` artifacts and explicitly records `model_training_performed=false` and `training_dataset_unlocked=false`. Phase 10.1 does not add a model runner.

## 3. Architecture boundary

```text
Accepted canonical ResearchDataBundle
          |
          v
Existing Phase 8 runner + shared backtest engine
          |
          v
Existing immutable experiment store
          |
          v
Existing artifact verifier (mandatory)
          |
          v
Phase 10 Research QA
  - admission/status guard
  - scenario registry and comparability checks
  - regime/period slicing
  - cost/fill stress aggregation
  - parameter sensitivity diagnostics
  - statistical and reproducibility checks
          |
          v
Research QA report (JSON + Markdown)
```

Phase 10 QA reads verified experiment artifacts. It does not mutate them, rerun accounting from ledgers, read CNEquity directly, build features/labels, train models, or silently substitute missing evidence. When a scenario requires different costs or execution assumptions, it invokes the existing runner/engine with an explicitly versioned configuration in a later implementation phase, then persists a new immutable run through the existing store.

## 4. Data-admission status guard

### 4.1 Four independent gates

Every QA report must carry the four gate statuses independently, including evidence identity and failure reasons:

| Gate key | Meaning | Required for price-only momentum evidence | Required for LightGBM training |
|---|---|---:|---:|
| `historical_universe` | PIT membership, listing/delisting, ST and survivorship evidence | PASS | PASS |
| `hfq` | Strict HFQ adjustment/factor coverage and revision-bound diagnostics | PASS | PASS |
| `pit_fundamentals` | Strict PIT values, exactness, publication-time and requested coverage | NOT_CONSUMED only when the tested strategy truly has no PIT dependency; otherwise PASS | PASS |
| `trading_status` | Historical status/risk-warning evidence and contradictions | PASS | PASS |

The current canonical bundle intentionally admits the price-only momentum benchmark using historical-universe, HFQ and trading-status gates; it records PIT as unused. This exception is not a model-training exception. QA must not relabel `NOT_CONSUMED` as `PASS`.

### 4.2 Guard decision states

The QA admission guard must fail closed and return one of:

- `BLOCKED`: missing/invalid acceptance report; window/profile mismatch; missing source revision/receipt; historical-universe, HFQ or trading-status gate not PASS; or required strategy inputs lack exact evidence.
- `PRICE_ONLY_ELIGIBLE`: the three required price-only gates PASS for the exact artifact window/profile/revisions and PIT is explicitly marked not consumed by the benchmark.
- `MODEL_TRAINING_BLOCKED`: any of the four gates is not PASS, regardless of benchmark eligibility.
- `MODEL_TRAINING_ELIGIBLE`: all four gates PASS for the exact same window, universe profile and source revisions, with the report identity/receipts retained. This is only an admission prerequisite; it does not itself create or unlock a training dataset.

For this project, a QA report may describe a price-only benchmark result only as conditional price-only evidence when its three applicable gates pass. It must never claim that the LightGBM strategy was tested, validated, or admitted while PIT or any other gate is not PASS. Overall research acceptance must remain BLOCKED if required evidence for the strategy under evaluation is absent.

### 4.3 Identity checks

Before any metric is emitted as research evidence, validate:

1. Acceptance report ID exists and is bound to the artifact.
2. Acceptance report window exactly covers the experiment's declared input window; each fold test window is inside it.
3. Universe profile matches exactly.
4. All required source revisions retain structured revision IDs and contract fingerprints; the adapter receipt dependencies must reproduce those identities, with historical universe bound to daily bars and price-limit rules independently versioned.
5. Actual adapter receipts, the receipt-provenance summary, and separate replayable historical-universe/HFQ acceptance receipts are retained and cross-checked.
6. Gate names are unique, known, and have explicit statuses; missing or unknown statuses are not PASS.
7. `training_dataset_unlocked=false` and `model_training_performed=false` remain true for the Phase 8 momentum artifacts.
8. Artifact verification succeeds before the manifest, fold table, or Parquet ledgers are used.

Do not infer gate PASS from a non-empty Parquet file, a successful CI run, a receipt alone, or a previous report for another window/revision.

## 5. Historical-period and market-regime protocol

### 5.1 Chronological period reporting

- The official exchange calendar and the existing non-overlapping walk-forward test folds are authoritative.
- Report every fold independently and also provide calendar-year summaries where at least 126 observed test sessions exist.
- Do not choose favorable start/end dates after viewing results.
- The pooled OOS result is secondary; fold distribution, worst fold and regime slices are mandatory.
- If a fold or calendar period has insufficient observations, report `INSUFFICIENT_SAMPLE`; do not merge it opportunistically into a favorable period.

### 5.2 Market-state labels

Use a predeclared cross-sectional market proxy derived only from the accepted point-in-time universe and HFQ prices, avoiding a new provider or an unversioned external index dependency.

At each exchange session (t):

1. For every security eligible in the PIT universe at (t), calculate its exact 63-session HFQ return using the session-(t) close and the close 63 exchange sessions earlier. No forward-fill; securities without both endpoints are excluded from the valid denominator.
2. Calculate (a) the median 63-session return across valid securities and (b) breadth, the fraction of valid securities with a positive 63-session return.
3. Require valid observations for at least 80% of the eligible universe at (t). Otherwise label the session `UNKNOWN` and exclude it from bull/bear/range attribution while keeping it visible in coverage diagnostics.
4. Label `BULL` when median return is at least +8% and breadth is at least 60%.
5. Label `BEAR` when median return is at most -8% and breadth is at most 40%.
6. Label all other sufficiently covered sessions `SIDEWAYS_OR_MIXED`.

These thresholds are fixed protocol constants, not tuned against strategy returns. The market-state calculation is trailing-only and must not use future observations. Regime attribution is calculated on daily OOS returns, then summarized by fold and experiment. Report session count and valid-universe coverage beside every regime metric. If a future implementation uses another regime definition, it must be a separately versioned protocol, not a silent change.

## 6. Scenario design

### 6.1 Transaction-cost stress

Use the exact persisted baseline cost configuration as the reference. Create cost scenarios by scaling each applicable configured fee/slippage component together; preserve the component attribution and do not double-deduct slippage.

| Scenario | Cost multiplier |
|---|---:|
| C0 — baseline | 1.0x |
| C1 — moderate | 1.5x |
| C2 — severe | 2.0x |
| C3 — extreme | 3.0x |

The report must show net return, excess return, CAGR, max drawdown, turnover, fees, slippage and the change from baseline. Values are explicit research assumptions, not a claim about the current broker/exchange schedule. Zero-cost results may be shown as a diagnostic only and must not be the acceptance basis.

Different cost configurations are not comparable through the existing Phase 8 parameter-comparison API. The QA layer must align them on the same acceptance identity, source revisions, universe, folds and strategy configuration, then compare their independently verified summaries.

### 6.2 Fill and execution stress

Pre-register target fill-ratio scenarios of 100%, 95%, 90%, 80% and 70%. Also report the observed baseline fill ratio and no-fill reasons.

A target fill ratio is a scenario request, not a result. The implementation must achieve it through an explicit, deterministic, order-level execution policy in the existing backtest engine (stable seed or deterministic hash, no future-return dependence), preserving partial/no-fill quantities, cash, holdings, fees and retry behavior. Never haircut a completed NAV series or randomly delete trades after accounting; that would produce invalid results.

If the current engine cannot represent a requested partial-fill policy without violating its accounting invariants, mark that scenario `UNSUPPORTED` and document the engine gap. Do not manufacture synthetic fill results or describe the current first-slice engine as a full historical exchange simulator. Keep suspension, limit locks, missing bars, lot sizes and blocked sells/buys as explicit reason codes.

## 7. Parameter sensitivity and out-of-sample discipline

- Freeze the current benchmark baseline at 126-session lookback and 21-session skip. Keep weekly rebalance, top 20, equal weight and no leverage fixed.
- A future sensitivity grid may vary only explicitly supported strategy parameters. Candidate lookback/skip perturbations must be pre-registered before evaluating the relevant test folds; unsupported parameters are not silently added to the current config.
- Never tune portfolio constraints or execution rules to improve the headline result.
- Parameter comparisons require identical acceptance identity, source revisions, universe profile, walk-forward configuration, test-fold dates and execution/cost configuration. Only the declared strategy parameter(s) may differ.
- The Phase 8 comparison output is diagnostic, not an optimizer or winner selector. Report the full variant distribution, median/worst variant, fraction of variants with positive excess return, and sensitivity relative to the frozen baseline.
- Use validation data for any parameter selection. Test folds remain untouched for final evaluation. If any test-fold result is used to choose a parameter, it is no longer an untouched OOS result and must be labeled exploratory; reserve a later chronological holdout for confirmation.
- No LightGBM hyperparameter or feature selection work is permitted before all four gates pass and a separately reviewed model-data builder exists.

## 8. Statistical and research-quality checks

At minimum, the QA report must include:

- Fold-level and regime-level observations, net returns, benchmark excess returns, CAGR, volatility, Sharpe, max drawdown, turnover, fill ratio and attributed costs.
- Mean, median, standard deviation, interquartile range, worst fold, positive-fold ratio and positive-excess-fold ratio.
- Sample counts and coverage; no metric without its denominator and observation count.
- Missing/duplicate sessions, non-finite values, ledger consistency and benchmark common-window alignment.
- Look-ahead/cutoff checks, feature/label timing checks when a model path exists, and evidence of PIT publication availability.
- Parameter multiplicity disclosure: number of variants examined and whether any were selected after seeing test results.
- Explicit caveat that Sharpe and positive excess return are descriptive unless a suitable dependence-aware inference procedure is implemented. Do not treat daily returns as independent IID observations by default.
- No universal pass/fail based on one Sharpe, CAGR, win rate or a single fold. Acceptance thresholds must be approved and frozen before empirical evaluation; Phase 10.1 defines reporting and guardrails, not performance cutoffs.

## 9. Unified QA report contract

Each QA run writes a new, immutable report directory or uniquely named pair of JSON/Markdown files. It must not overwrite Phase 8 artifacts.

Required sections:

1. **Identity** — QA schema/protocol version, QA ID/time, repository commit, experiment IDs, source artifact hashes.
2. **Admission** — all four independent gate statuses, strategy dependency profile, acceptance report ID/window, universe profile, source revisions/receipts, guard decision and blockers.
3. **Integrity** — verifier result, manifest/config hashes, ledger row counts, fold/test cutoff checks, coverage and accounting consistency.
4. **Experiment design** — frozen baseline, fold definitions, calendar window, strategy/backtest configs and all pre-registered scenario IDs.
5. **Period/regime results** — per fold, calendar year and market-state results with session counts and coverage.
6. **Cost stress** — each cost multiplier, net/excess return and risk/cost deltas versus baseline.
7. **Execution stress** — requested and achieved fill ratios, order counts, partial/no-fill counts and reason codes; unsupported scenarios clearly marked.
8. **Parameter sensitivity** — complete variant list, comparison constraints, full distribution and baseline-relative deltas.
9. **Statistical QA** — sample size, dispersion, uncertainty method if implemented, multiplicity and exploratory/confirmatory designation.
10. **Limitations** — missing evidence, unsupported execution features, current engine gaps and all deviations from the frozen protocol.
11. **Conclusion** — `BLOCKED`, `INSUFFICIENT_EVIDENCE`, `FRAGILE`, or `ROBUSTNESS_EVIDENCE_ONLY`; never state future profitability or production-grade execution.

Suggested machine-readable top-level fields:

```json
{
  "schema_version": 1,
  "protocol_version": "phase10.1",
  "qa_id": "...",
  "created_at": "...",
  "experiment_ids": [],
  "admission": {
    "decision": "BLOCKED",
    "gates": {
      "historical_universe": "FAIL",
      "hfq": "FAIL",
      "pit_fundamentals": "FAIL",
      "trading_status": "FAIL"
    },
    "training_dataset_unlocked": false,
    "model_training_performed": false
  },
  "integrity": {"verified": false},
  "scenarios": [],
  "periods": [],
  "regimes": [],
  "limitations": [],
  "conclusion": "BLOCKED"
}
```

The JSON example is a schema illustration, not a report of the user's current data status.

## 10. Phase 10.1 acceptance criteria

Phase 10.1 is complete when:

- [ ] Existing Phase 8 runner, persistence, verifier, comparator and fold planner are documented as the sole execution/evidence interfaces.
- [ ] No duplicate experiment store, scheduler, database, dashboard or backtest engine is proposed.
- [ ] Four-gate statuses and the price-only benchmark exception are explicit; `NOT_CONSUMED` is never coerced to PASS.
- [ ] The guard's identity checks and fail-closed decisions are specified.
- [ ] Calendar-period and trailing-only market-regime rules are fixed and versioned.
- [ ] Cost and fill stress matrices, comparability rules and unsupported-scenario behavior are explicit.
- [ ] Parameter sensitivity and untouched OOS rules are explicit.
- [ ] Unified JSON/Markdown report fields and conclusion states are specified.
- [ ] Current engine limitations are disclosed; unsupported fills are not fabricated.
- [ ] No data backfill, model training, or out-of-scope feature is triggered.

## 11. Implementation sequence after 10.1

1. **P10.2 — admission guard + report skeleton:** implement read-only gate/identity validation and QA report schema tests; no backtest changes.
2. **P10.3 — period/regime diagnostics:** derive labels from accepted bundle data and aggregate verified OOS ledgers.
3. **P10.4 — cost stress:** run pre-registered cost variants through the existing runner/engine and persist immutable artifacts. Implemented on `phase10.4-cost-stress-qa`; awaiting CI and review.
4. **P10.5 — fill stress:** first inspect engine support and accounting tests; extend only the existing execution policy if necessary, otherwise report unsupported.
5. **P10.6 — parameter and statistical QA:** pre-register variants, preserve untouched test windows, add dependence-aware inference only with explicit methodology.
6. **P10.7 — integrated acceptance:** evaluate the complete report contract and publish limitations; strategy acceptance remains separate from data admission.

No phase after 10.1 may relax the frozen project scope or four-gate model-training lock.


## 10.6 — Descriptive statistical QA

`build_statistical_qa_summary(experiment_path)` verifies the Phase 8 artifact
and reports fold-level distributions for returns, CAGR, volatility, Sharpe,
drawdown and attributed costs, including sample counts, missing counts, mean,
median, sample standard deviation, quartiles/IQR, min/max and worst fold. It
also reports OOS session coverage, observed share fill ratio and no-fill reasons.

The summary is explicitly `DESCRIPTIVE_ONLY`. It does not treat daily returns
as IID, calculate p-values or confidence intervals, or infer benchmark excess
when no independent benchmark fold ledger is attached. Parameter multiplicity
and post-test selection history remain `NOT_ASSESSED` until a pre-registered
scenario registry exists. Admission failure blocks metric emission.

## 10.3 — Period and market-regime diagnostics

The P10.3 implementation is exposed as
`build_period_regime_diagnostics(experiment_path, bundle)` and is integrated
into `build_research_qa_report(..., bundle=bundle)`. Without the optional
bundle argument, the report remains a P10.2 admission skeleton. With it, the
report includes period/regime summaries and a SHA-256 digest for the P10.3
diagnostic payload. The P10.3 builder invokes the Phase 8 artifact verifier
and the P10.2 fail-closed admission assessment.
The supplied builder-validated canonical bundle must match the experiment's
acceptance report ID, requested window, universe profile, four independent gate
statuses, structured source revisions, adapter receipts, receipt summary,
acceptance receipts, coverage audits, limit-evidence identity, benchmark gates
and declared data scope exactly.

Period reporting includes every walk-forward test fold and calendar-year
summaries. The default minimum for an interpretable period summary is 126
observed OOS sessions; shorter samples remain present and are marked
`INSUFFICIENT_SAMPLE`, never merged into another period.

Market-state labels use exact HFQ closes and a trailing 63-exchange-session
return across the PIT-eligible universe on each session. At least 80% valid
universe coverage is required. BULL/BEAR thresholds are fixed at median
63-session return >= +8% with breadth >= 60%, and <= -8% with breadth <= 40%.
Other sufficiently covered sessions are SIDEWAYS_OR_MIXED. Missing daily
membership, insufficient lookback, or insufficient price coverage produces
UNKNOWN; no forward fill or future observations are used.

The canonical bundle may contain weekly-only membership snapshots. P10.3
deliberately does not carry these snapshots forward to invent daily PIT
membership. Such sessions remain UNKNOWN until the input bundle contains
verified daily membership snapshots. P10.3 only reads verified OOS ledgers
and the matching bundle; it does not rerun the strategy, read CNEquity, or
train a model. PIT failure continues to block LightGBM regardless of
price-only benchmark eligibility.


## 10.4 — Transaction-cost stress implementation

The implementation is exposed as `build_cost_stress_scenarios(baseline)`
and `run_cost_stress_suite(bundle, baseline_experiment_path, ...)`.
The caller must provide the exact persisted C0 baseline experiment. Its
artifact verifier and P10.2/P10.3 admission checks run before any stress
variant is evaluated; the baseline is not rerun or overwritten.

The fixed protocol applies one multiplier to every configured fee and
slippage component together:

| Scenario | Multiplier |
|---|---:|
| C0 — baseline | 1.0x (exact persisted baseline) |
| C1 — moderate | 1.5x |
| C2 — severe | 2.0x |
| C3 — extreme | 3.0x |

C1–C3 are run through `run_walk_forward_momentum` and persisted as separate
immutable Phase 8 experiments. Each artifact is verified independently.
Before aggregation, the suite requires identical data provenance/revisions,
walk-forward config, strategy config and fold windows; only the cost fields
may differ. The Phase 8 parameter comparator is not weakened or reused for
these unlike cost configurations.

The summary reports OOS net return, CAGR, worst-fold drawdown, turnover,
itemized commission/transfer fee/stamp duty/slippage, total costs, order
counts and deltas versus C0. `excess_return_vs_C0` is explicitly the return
difference caused by the cost assumption, not market-benchmark excess return.
When OOS test folds contain gaps, returns/CAGR use the mean fold summaries
and disclose that gaps are excluded; no continuous portfolio path is implied.

Multipliers are research assumptions, not a representation of current
broker/exchange fee schedules. Slippage remains embedded in the worse fill
price and is reported separately; it is never deducted from cash a second
time. The suite does not infer actual commissions, taxes, transfer charges or
slippage from incomplete data. It does not modify existing experiment
artifacts, perform CNEquity reads/backfills, train a model, or unlock training.

## 10.5 — Fill-stress capability assessment

The shared engine now exposes `BacktestConfig.target_fill_ratio` (0.0–1.0,
default 1.0). At the first eligible execution session for each signal/instrument
transition, it freezes the full target difference and a cumulative fill cap.
For ratios below 100%, the cap is rounded down to the configured board lot.
Temporary no-fills and cash-affordability partial fills retry only the remaining
quantity up to that cap; reaching the cap ends execution for that transition.
A cap below one board lot is explicitly recorded as a no-fill. A new signal
creates a new transition and cap. The default 1.0 path preserves the full target
quantity.

The order ledger distinguishes per-attempt `requested_qty`, full
`target_requested_qty`, `target_fill_cap_qty`, and `target_fill_ratio`.
Retries share one (signal date, instrument) denominator. Cumulative fills,
stable target quantities/caps and configured ratios are validated. Legacy
persisted ledgers without transition identity retain an explicitly labeled
`attempt_requested_legacy` ratio and are not comparable to the new denominator.

`build_fill_stress_assessment(experiment_path)` remains a read-only baseline
capability check; it marks target scenarios `UNSUPPORTED` until an immutable
scenario suite is supplied. `run_fill_stress_suite(...)` runs F1–F4 through the
existing walk-forward runner, keeps the exact persisted F0 baseline, verifies
every artifact and admission decision, checks provenance/config/fold alignment,
and reports achieved ratios separately from configured caps. Board-lot rounding,
suspensions, price-limit locks, missing bars and cash constraints can make the
achieved ratio lower than the requested scenario. It does not delete trades or
haircut NAV.
## 10.7 — Integrated immutable QA report

`build_integrated_research_qa_report(...)` combines the verified P10.2 admission
and integrity report with optional P10.3 period/regime diagnostics, a supplied
verified P10.4 cost-stress report, P10.5 fill-stress capability assessment and
P10.6 descriptive statistical QA. It never launches cost scenarios; the P10.4
report must already contain verified C0-C3 artifacts and match the same baseline
experiment and repository commit.

`write_research_qa_report(report, output_root=...)` writes `report.json` and
`report.md` under a new QA-ID directory using exclusive creation. Existing QA
directories are never overwritten. The writer returns both file hashes.

The integrated conclusion remains `BLOCKED` when admission fails and
`INSUFFICIENT_EVIDENCE` otherwise until execution fill-ratio scenarios are
supported and acceptance thresholds are pre-registered. It does not claim
`FRAGILE` or `ROBUSTNESS_EVIDENCE_ONLY` from descriptive metrics alone.
