# Backtest rules

## Portfolio contract

- 20 target holdings by default, nominally 5% each with 20 valid candidates;
  equal-weight among fewer valid candidates.
- Weekly rebalance using the final supplied exchange session in each ISO week.
- No leverage; gross target exposure cannot exceed 100%.
- Model strategy and momentum benchmark share the same engine/configuration.

## Signal and execution timing

- A signal using close(t) is created after that session and cannot execute at
  close(t).
- First execution attempt is the next supplied session's open.
- Targets remain active until replaced by a newer signal; blocked target
  differences may be retried on later sessions under the same target.
- The official exchange calendar must be supplied by the future research
  bundle; dates observed for one selected stock are not a calendar.
- Targets still requiring execution at the end of the supplied window are
  exposed separately in `BacktestResult.pending_targets` and the execution
  report. A signal on the final supplied session is marked `no_next_session`,
  not silently treated as filled or discarded.

## Order lifecycle

Represent target selection, order intent, eligibility, requested quantity,
filled quantity/price, commission, transfer fee, stamp duty, slippage
cost, and no-fill/partial-fill reason separately.
An unfilled order is not a trade. Existing holdings remain until a valid sell
executes. Sells are attempted before buys so proceeds can fund buys on the same
simulation session.

## A-share constraints: implemented first slice

- Suspension flag and missing bars are no-fill conditions.
- Explicit locked limit-up buys and locked limit-down sells are no-fill.
- Supplied limit prices are checked against the attempted open.
- Buy quantities are rounded down to the configured 100-share lot by default.
- Cash availability, commission/minimum commission, sell stamp duty, and
  transfer fee are reflected in cash accounting.
- Slippage is embedded in fill prices relative to the raw open; the order ledger
  records its cost separately for reporting and does not deduct it twice.
- `fees` is the sum of commission, transfer fee, and stamp duty; `slippage_cost`
  is a separate execution cost.

## Explicitly not yet fully modeled

- Exchange settlement cash availability and broker-specific settlement details beyond the ledger's same-session buy/sell prohibition.
- Full board/date-specific daily price-limit regimes and IPO no-limit windows.
- Derivation/verification of lock states from authoritative intraday/market data.
- Corporate-action share/cash adjustments and dividends.
- Historically authoritative odd-lot sell/liquidation rules.
- A complete instrument-by-calendar status matrix, including every suspension.

The current engine is a deterministic first slice, not a full historical A-share
exchange matching simulator. Cost configuration values are experiment
assumptions and must be versioned with every run, not presumed to be a current
broker/exchange fee schedule.

## Accounting invariants

- Cash plus marked-to-market positions equals NAV.
- No negative cash; no leverage; gross exposure <= 100%.
- Fees reduce cash and NAV exactly once.
- Every order has an auditable fill, partial-fill, or no-fill status.
- Missing data cannot silently create a fill.
