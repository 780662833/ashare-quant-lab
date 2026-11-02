# Research protocol (v1)

## Questions
1. Does the model rank stocks with genuine out-of-sample predictive power?
2. Does excess return survive realistic costs and unfilled-order constraints?
3. Does performance persist across distinct chronological market regimes?

## Frozen experiment design
- One LightGBM cross-sectional model, top 20.
- One trailing-return momentum benchmark, top 20.
- Weekly rebalance; signal after close, execution no earlier than next eligible session.
- Equal-weight target; no leverage.
- Chronological walk-forward; training ends before validation/test begins.
- Identical point-in-time universe and execution engine for both strategies.

## Required outputs
- Daily NAV/returns and holdings snapshots.
- Order/trade ledger including rejected and partially filled orders.
- Benchmark-relative return, annualized return/volatility, Sharpe, max drawdown,
  turnover, exposure, and transaction-cost breakdown.
- Fold-level and regime-level metrics, not aggregate metrics alone.

## Anti-bias rules
- Never use current constituents as the historical universe.
- Never use revised fundamentals before actual publication/availability time.
- Never use a feature later than the decision timestamp.
- Never execute at a price unavailable after the decision.
- Never silently replace unfilled selections with better-performing securities.
- Record experiment configs, data versions, and model seeds.

Exact decision/execution calendar, label horizon, date-specific price-limit
rules, and corporate-action treatment must be verified before the backtest is
considered research-grade.
