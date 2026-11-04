# Weekly momentum benchmark

The MVP benchmark is a weekly, cross-sectional, top-20 equal-weight momentum
portfolio. It is deliberately a transparent baseline, not a learned strategy.

## Signal definition

For signal date t, the default score is:

`momentum(t) = HFQ_close(t - 21 sessions) / HFQ_close(t - 126 sessions) - 1`

This is a 126-session lookback that skips the most recent 21 sessions
(approximately a 6-to-1 momentum convention). Scores are ranked descending;
ties are broken by exchange-qualified instrument code. The top N names receive
equal target weights. If fewer than N valid members have both exact endpoint
prices, the portfolio weights are divided equally among the available names.

## Point-in-time inputs

The function requires three explicit inputs:

- Adjusted bars: `instrument`, `date`, and positive `adj_close`. This field
  is used for signal calculation only; it must be CNEquity exact HFQ output.
- Universe membership: `instrument` and `date`, representing the eligible
  historical universe known on each weekly signal date. Current constituents
  must never be backfilled into old dates.
- Exchange trading calendar: ordered market sessions, used to locate the exact
  lookback and skip dates. Calendar dates are not inferred from whichever
  symbols happen to have prices.

The strategy inner-joins exact endpoint prices. A security missing either
endpoint is excluded for that rebalance; it is not forward-filled, and raw
close is not used as a substitute. Only membership as-of the signal date is
eligible. Future prices cannot affect earlier scores.

## Execution

The strategy returns only `signal_date`, `instrument`, and `weight`
targets. Feed those targets to the common backtest engine, which observes the
signal after that close and attempts execution at the next eligible session
open using raw OHLC and explicit suspension/price-limit evidence.

The model strategy and momentum benchmark must use the same historical
universe, rebalance dates, execution rules, cost model, and report function.
No model training or data-gate bypass is part of this benchmark.
