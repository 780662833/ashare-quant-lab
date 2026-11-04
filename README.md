# ashare-quant-lab

A reproducible A-share quantitative **research** MVP. The goal is to test
whether a cross-sectional stock-selection model has persistent, executable,
net-of-cost alpha—not to build an automated trading platform.

## Frozen v1 scope

- One weekly cross-sectional model strategy (LightGBM).
- One transparent weekly momentum benchmark (126-session lookback, 21-session skip).
- Top 20 holdings, equal-weight target allocation, no leverage.
- Historical backtest, walk-forward evaluation, daily holdings, trades, and
  return/risk reports.
- No live trading, LLM decisions, ETFs, portfolio optimizers, multi-strategy
  management, or complex dashboard.

## Architecture

- **CNEquity 0.21.0**: sole authoritative source for historical market,
  universe, adjustment, trading-status, and PIT facts.
- **DuckDB + Polars**: local analytical queries and deterministic transforms.
- **Qlib + LightGBM**: optional research/model workflow; training remains gated.
- **Native backtest module**: shared A-share execution, cost, holdings, and
  accounting engine for both model and benchmark strategies.
- **Report module**: NAV performance, risk, turnover, fill, costs, and
  benchmark-relative summaries.

Raw OHLC is for execution; HFQ adjusted prices are for research signals.
Strategies emit target weights and never place orders or query CNEquity
directly. See the system-level architecture before extending a component.

## Documentation index

- [Architecture and engineering contract](docs/architecture.md) — components,
  boundaries, data/time rules, acceptance gates, simulation limitations.
- [Development roadmap](docs/development_roadmap.md) — phase status, scope,
  exit criteria, and next engineering work.
- [Canonical research data bundle](docs/research_data_bundle.md) — data assembly,
  acceptance report identity, provenance, and benchmark orchestration.
- [Data contract](docs/data_contract.md) — CNEquity fields, PIT, four-gate admission.
- [Research protocol](docs/research_protocol.md) — experiment fairness and anti-bias rules.
- [Backtest rules](docs/backtest_rules.md) — timing, fills, accounting, known gaps.
- [Momentum benchmark](docs/momentum_benchmark.md) — weekly 126/21 signal definition.
- [Backtest report](docs/backtest_report.md) — metric definitions and caveats.

## Development

Install the package and development dependencies:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Install model dependencies only when entering the model stage:

```bash
python -m pip install -e ".[research]"
```

CI tests use synthetic data and do not certify the local CNEquity lake. See
[docs/data_contract.md](docs/data_contract.md) for local data acceptance.

## Research integrity

- Every feature must be knowable at the simulated decision timestamp.
- Labels must use strictly future outcomes and be separated from features.
- Train/validation/test splits are chronological; no random time-series split.
- Universe membership is point-in-time, including delisted securities.
- Costs, execution restrictions, and unfilled orders must remain visible.
- Model and momentum benchmark use the same universe, schedule, execution,
  cost model, and report window.
- Training remains locked until all four data acceptance gates pass for the
  exact same window and data revisions; a PASS report alone does not unlock it.
