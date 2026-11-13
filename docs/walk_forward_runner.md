# Walk-forward momentum benchmark runner (Phase 8)

## Scope

This phase runs the existing deterministic weekly momentum benchmark through
the chronological folds produced by the Phase 6 planner. It does not train a
model, create features or labels, tune parameters, or relax any data gate.
LightGBM/Qlib are not required.

The runner accepts an already validated `ResearchDataBundle`. It does not
assemble a bundle from CNEquity or manufacture historical price-limit facts.
The bundle must already contain explicit raw execution bars, exact HFQ prices,
dated PIT universe snapshots, official sessions, execution-state evidence,
versioned limit evidence, source revisions, and the required price-only
benchmark admission receipts.

## Fold replay semantics

For each fold:

1. Read the fold's test start/end and find the immediately preceding exchange
   session. That prior session's closing NAV is the fold's initial-NAV baseline.
2. Create a prefix view of the bundle ending exactly on `test_end`. Raw bars,
   adjusted bars, universe snapshots and the calendar passed to the strategy
   contain no later observations.
3. Replay the deterministic momentum rule from the beginning of the available
   bundle history through `test_end`. This preserves the benchmark holdings
   and cash that the same rule would have carried into the test window. The
   strategy is not fitted or selected on those observations.
4. Keep only test-window daily NAV, orders and holdings in the fold output.
   The first test-session return is measured against the preceding session's
   close, so the reported initial NAV is the separate `baseline_nav`.
5. Calculate the fold report from that baseline and the test-session returns.
   Cost, fill and no-fill statistics include test-window orders only.

This is a continuous replay of a fixed rule, not a fresh-cash restart on every
fold. The cutoff is enforced separately for each fold so a later observation
cannot alter an earlier fold result.

## Outputs

`run_walk_forward_momentum(...)` returns:

- `folds`: fold definition, prior-session NAV baseline, test-only daily rows,
  test-only orders/holdings, metrics and cutoff/provenance metadata.
- `fold_table`: one compact row per fold with return, CAGR, volatility, Sharpe,
  drawdown, order count and transaction costs.
- `oos_daily`, `oos_orders`, `oos_holdings`: concatenated test-window
  ledgers only.
- `aggregate_report`: mean/median fold return, positive-fold ratio, average
  fold metrics, worst fold drawdown, linked fold return, total test-window
  orders/no-fills and itemized costs.

The linked return compounds only test-window returns. When test windows are
contiguous, it represents the continuous fixed-rule replay across those
out-of-sample sessions. When a custom `step_sessions` leaves gaps, the linked
return excludes the gap sessions and is explicitly not a full calendar-period
portfolio return. Fold-level results remain the primary diagnostic.

The first test-day return is included in each fold's total return and
annualized-return calculation. The baseline date is not counted as a test
session. Cumulative return and maximum drawdown use the prior close as a
separate baseline followed by every actual test-session NAV; the baseline is
never substituted for the first test NAV. Daily-return volatility and Sharpe
use only the actual test-session returns, including the first test day. This
also keeps one-session test folds' return and drawdown metrics correct.

## Configuration and use

The Phase 6 defaults are in `configs/walk_forward.yaml`: 504 train sessions,
63 validation sessions, 63 test sessions, a five-session label horizon and
one-session embargo. For the momentum-only baseline, the fold planner's
training/validation boundaries define the evaluation schedule; the runner
still does not build training labels.

Example API:

```python
from ashare_quant.research import (
    WalkForwardConfig,
    run_walk_forward_momentum,
)
from ashare_quant.strategies.momentum import MomentumConfig

run = run_walk_forward_momentum(
    research_bundle,
    walk_forward_config=WalkForwardConfig(),
    strategy_config=MomentumConfig(),
)

print(run.fold_table)
print(run.aggregate_report)
```

Use the same fold definitions, test windows, data bundle revisions, backtest
configuration and cost model when a future model strategy is added. That
model path remains blocked until the four CNEquity acceptance gates pass for
the same window, universe profile and source revisions and a separately
reviewed training-data builder enforces that admission.

## Evidence boundary

Unit tests use synthetic inputs. A passing CI run validates deterministic
orchestration and cutoff behavior only; it does not certify the local
CNEquity lake, the completeness/correctness of a real historical price-limit
provider, or empirical strategy performance. T+1, corporate actions, complete
security-by-session trading status and other documented exchange-simulation
gaps remain.
