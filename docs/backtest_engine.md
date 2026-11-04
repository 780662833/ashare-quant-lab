# Phase 2: deterministic backtest core

The first backtest slice is independent of CNEquity and Qlib so it can be
tested with synthetic fixtures while the local lake is populated in the
background.

## Input contracts

Daily bars use canonical fields: instrument, date, open, high, low, close,
volume, amount, is_suspended, is_limit_up_locked, is_limit_down_locked,
limit_up, and limit_down.

Execution-state evidence is mandatory. Unknown suspension/limit-lock state is
not treated as tradable. Price-limit values may be null when no applicable
limit exists on that session.

Targets contain signal_date, instrument, and weight. Weights must be
non-negative and sum to no more than 100% for each signal date. A signal is
observed after the signal-date close and first becomes executable at the next
session open. The latest target remains active; unfilled differences are
retried on later sessions until a new signal replaces it.

## Execution and accounting

- Sells are attempted before buys so sale proceeds can fund purchases.
- Buys are rounded down to the configured board lot (100 shares by default).
- No leverage or negative cash is permitted. Buy quantities are reduced to
  affordable board lots after costs.
- Suspended securities and locked limit-up buys / locked limit-down sells are
  recorded as no_fill, with a reason. Missing bars also never imply a fill.
- Slippage, commission (including minimum commission), sell stamp duty, and
  transfer fees are charged once per filled order.
- Existing holdings remain in the portfolio when a sell cannot execute.
- Missing daily bars for held positions use the last available close for
  marking, but cannot create an execution.

equal_weight_targets produces deterministic top-N holdings with stable symbol
tie-breaking. weekly_rebalance_dates returns the final available session of
each ISO week.

## Deliberate limitations of this slice

This is not yet a production-grade exchange simulator. Corporate-action
share/cash adjustments, full A-share odd-lot liquidation semantics, board-
and-date-specific price-limit rules, IPO no-limit periods, settlement
calendars, and complete historical status joins are subsequent work. The
engine requires its execution facts explicitly and fails closed when required
status flags are missing. No live CNEquity or model data is read here.
