# ashare-quant-lab

A reproducible A-share quantitative research MVP. The goal is to test whether a
cross-sectional stock-selection model has persistent, executable, net-of-cost
alpha—not to build an automated trading platform.

## Frozen v1 scope

- One weekly cross-sectional model strategy (LightGBM).
- One simple weekly momentum benchmark.
- Top 20 holdings, equal-weight target allocation, no leverage.
- Historical backtest, walk-forward evaluation, daily holdings, trades, and
  return/risk reports.
- No live trading, LLM decisions, ETFs, portfolio optimizers, multi-strategy
  management, or complex dashboard.

## Architecture

- **CNEquity**: authoritative source for historical market/universe/fundamental
  facts and point-in-time availability. Integration is isolated behind an adapter.
- **DuckDB + Polars**: research queries and deterministic data transformations.
- **Qlib + LightGBM**: feature dataset/model training and prediction workflow.
- **Native backtest module**: A-share execution constraints, costs, holdings,
  and accounting.

No second source of truth for market data is allowed. Do not assume a trade
filled when the security is suspended or locked at an unfillable price limit.

## Development

Install the package and development dependencies:

```bash
python -m pip install -e ".[dev]"
pytest
```

Install model dependencies when entering the model stage:

```bash
python -m pip install -e ".[research]"
```

CNEquity is intentionally not pinned as a guessed PyPI dependency. Its actual
supported installation/API contract must be inspected and documented before
the data integration stage.

## Research integrity

- Every feature must be available at the simulated decision timestamp.
- Labels must use strictly future outcomes and be separated from features.
- Train/validation/test splits are chronological; no random time-series split.
- Universe membership is point-in-time, including delisted securities.
- Corporate actions, suspensions, price limits, lot sizes, and costs must be
  represented explicitly.
- Failed/unfilled orders remain visible; they are never silently dropped.
- Model and momentum benchmark use the same universe, schedule, execution
  assumptions, and cost model.

See [docs/research_protocol.md](docs/research_protocol.md),
[docs/data_contract.md](docs/data_contract.md), and
[docs/backtest_rules.md](docs/backtest_rules.md).
