# Backtest rules

## Portfolio
- 20 target holdings, nominally 5% each, no borrowing/leverage.
- Weekly rebalance.
- Model and momentum benchmark share the same accounting/execution engine.

## Order lifecycle
Represent target selection, order intent, eligibility, fill quantity/price,
fees, and rejection/no-fill reason separately. An unfilled order is not a trade.
Existing holdings remain until a valid sell executes.

## A-share constraints
- Suspension and missing/invalid quotes.
- Daily price limits and locked limit-up/limit-down conditions.
- Board-lot quantities and cash availability.
- Sell-side stamp duty, commissions/minimum commission, transfer fees, slippage.
- Corporate actions and cash dividends.

## Timing
A signal using close(t) cannot fill at close(t). Provisional reference is the
next eligible session open, subject to eligibility and price-limit checks.

## Accounting invariants
- Cash plus marked-to-market positions equals NAV.
- No negative cash; no leverage; gross exposure <= 100%.
- Fees reduce cash and NAV exactly once.
- Every order has an auditable terminal status.
