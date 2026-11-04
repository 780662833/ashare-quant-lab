# Phase 3: backtest performance report

The report layer consumes the deterministic engine's `BacktestResult`; it
does not fetch data, train models, or alter portfolio accounting.

## Metrics

- Period start/end and observed session count.
- Total return, CAGR, annualized daily-return volatility, maximum drawdown,
  and annualized Sharpe ratio (risk-free rate defaults to zero).
- Order counts, share fill ratio, traded notional, and gross turnover
  (sum of filled buy and sell notional divided by average daily NAV).
- Total recorded transaction fees and fees in basis points of initial NAV.
- Optional benchmark return and strategy excess return over the common
  observed first/last sessions.

The report preserves no-fill orders in the execution counts. Turnover counts
both buys and sells, so it is gross traded notional rather than one-way
turnover. The engine currently records aggregate fees, not separate commission,
stamp-duty, and transfer-fee columns; the report does not fabricate that split.

## Benchmark contract

Pass a daily Polars frame with `date` and `nav`. It must overlap the
strategy on at least two sessions. Strategy and benchmark cumulative returns
are recalculated over the same common start/end dates. A positive excess
return is descriptive and is not, by itself, evidence of statistically
significant alpha.

## Limitations

Metrics are descriptive, not a claim that the current execution engine fully
models all historical A-share exchange rules. Corporate actions, board/date-
specific price limits, IPO no-limit periods, T+1 settlement, and complete
historical trading-status evidence remain separate engine/data-contract work.
No report can unlock model training; the four data acceptance gates remain
mandatory and `training_dataset_unlocked` remains false.
