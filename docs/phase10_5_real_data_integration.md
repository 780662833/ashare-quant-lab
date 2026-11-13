# Phase 10.5 — CNEquity runtime compatibility and real-data integration

## Current boundary

The research runner consumes an already validated `ResearchDataBundle`; it does not
assemble that bundle from a CNEquity lake. The current adapter and acceptance code
were written against the repository's pinned CNEquity version. A local installation
reported as CNEquity 0.23.0 is not evidence that the 0.21.0 adapter contract remains
compatible.

The execution-evidence layer also requires independently sourced historical price-limit
facts. It intentionally does not infer limit prices or locked-limit states from close
prices, current board rules, or ticker labels. Without complete, date-specific evidence,
the execution bundle must remain blocked.

## First executable step: inspect the installed runtime

Run from the repository root with the project's Python environment:

```text
python scripts/inspect_cnequity_runtime.py
```

The script is read-only. It reports:

- the CNEquity requirement declared in `pyproject.toml`;
- the installed CNEquity version;
- importability and callable signatures for the exact API symbols currently used by
  the adapter/acceptance implementation;
- missing symbols and import errors.

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | Declared and installed versions match, and the expected API symbols import. This is not data-contract acceptance. |
| 2 | Version mismatch or incomplete API surface; compatibility remains unverified. |
| 3 | CNEquity is not installed. |

The script does not load datasets, call CNEquity query functions, access the network,
write reports, backfill data, or run experiments. A successful result only narrows
the compatibility investigation; it does not prove that 0.23.0 returns the same
schemas, semantics, receipts, or historical coverage as 0.21.0.

## CNEquity 0.23.0 source-level compatibility findings (2026-11-13)

The installed runtime report supplied for the research environment confirms that the
0.23.0 API symbols import and exposes these signatures. Source inspection of the
official v0.23.0 tag additionally confirms:

- `load_with_receipt()` returns `ReadResult(frame: pl.DataFrame, receipt: dict)`;
  its receipt distinguishes observed row bounds from completeness and records
  dependency revisions plus replayability.
- `traded_status_evidence()` returns a `pl.LazyFrame` with the fields consumed by
  the current trading-status acceptance gate, including `asset_class`, `exclusion`,
  `has_status`, `risk_warning_known`, and `contradiction`.
- The 0.23.0 migration changes `daily_bars` and `adj_factors` partition granularity
  from day to month while preserving their columns and primary keys. The project
  adapter reads these datasets through CNEquity's public query API rather than
  constructing Hive paths itself; the internal trading-status quality API also
  owns its partition scan. Do not add direct Parquet path assumptions to the assembler.
- Strict PIT reads retain the canonical `financial_statement_items.item_code`
  field and add `pit_is_exact` / `pit_quality`. The acceptance implementation had
  incorrectly checked an `item` column, which would fail closed even when the
  returned CNEquity PIT rows were valid. The acceptance helper now checks
  `item_code` and still requires exact strict rows for every requested symbol/item
  pair.

This is source-level evidence, not an end-to-end test against the user's installed
package or lake. The declared `cnequity==0.21.0` pin is intentionally unchanged.
The 0.23.0 runtime remains **unverified** until the adapter and acceptance path are
exercised against the actual local lake with replayable receipts and coverage checks.

## Receipt-aware adapter reads (2026-11-13)

The CNEquity adapter now uses `load_with_receipt()` for every dataset read and
sets `require_replayable=True`. It rejects a result unless it contains a Polars
DataFrame and a receipt explicitly marked `replayable: true`. Receipts are retained
on the adapter instance as ordered `source_receipts` entries, including the dataset
name, so repeated reads of the same dataset are not silently overwritten.

The adapter exposes an explicit `trading_calendar(start_date, end_date)` read
that filters the receipt-backed `trading_calendar` dataset to rows marked
`is_trading=true`. It rejects null/duplicate dates, out-of-window rows, and
windows with no sessions. This provides the official session list and pins the
calendar dependency in the same adapter read session; it does not certify that
the returned calendar is complete without the separate coverage audit.

This closes the adapter-level provenance gap; it does **not** complete the assembler.
The assembler must still map those receipts to the exact input frames and validate
revision dependencies, date-window support, and cross-dataset consistency. Unit tests
use deterministic fake `ReadResult` objects and are not evidence of local-lake behavior.

## Required Phase 10.5 work sequence

1. **Version/API compatibility.** Capture the runtime report from the actual research
   environment. For a version mismatch, inspect the installed 0.23.0 signatures and
   documented dataset schemas before changing the dependency pin. Add contract tests
   using real, non-sensitive schema samples. Do not widen the supported version range
   solely because imports succeed.
2. **Dataset contract verification.** Verify daily bars (raw OHLC plus exact HFQ fields),
   historical universe snapshots by signal date, official trading sessions, historical
   trading status, and replayable source receipts/revisions. Record supported date
   windows separately from merely observed rows.
3. **Historical execution-limit evidence.** Select and document a verifiable provider
   for historical limit prices, board/regime rules, and up/down locked states. Evidence
   must cover every execution bar and retain source, rule version, and regime metadata.
   If no provider or coverage exists, fail closed; do not synthesize lock flags.
4. **Real-data bundle assembler.** Build one explicit assembler that joins the verified
   source datasets and receipts into the existing `ResearchDataBundle` contract.
   Validate key uniqueness, session coverage, per-date PIT universe membership,
   adjustment provenance, execution-state coverage, and replayability before returning
   a bundle.
5. **Unified experiment entry point.** Only after steps 1–4 pass, add a single command
   that runs preflight, assembles the bundle, executes the existing weekly momentum
   walk-forward, and persists the immutable experiment manifest and artifacts.
6. **Empirical acceptance.** Run on the user's actual lake and save the acceptance
   report, source receipts/revisions, bundle summary, manifest, hashes, and output row
   counts. Synthetic unit tests remain separate evidence and must not be described as
   a real historical experiment.

## Research gates remain unchanged

- The price-only momentum benchmark may proceed only when historical universe,
  HFQ adjustment, and trading-status gates pass, with replayable historical-universe
  and HFQ receipts and complete execution evidence.
- PIT fundamentals are an independent gate. PIT failure may leave the price-only
  benchmark eligible, but must not unlock LightGBM training.
- No training, backfill, live trading, strategy expansion, or automated trading is
  part of this phase.
- A CI pass proves the tested code path passes CI; it does not prove local CNEquity
  compatibility, actual-lake coverage, or a real backtest.

## Current status

This phase is **not yet cleared for a real-data experiment**. The runtime preflight
is the first diagnostic increment. The installed 0.23.0 environment, local lake,
historical limit-evidence provider, and end-to-end real-data assembler still require
independent verification.
