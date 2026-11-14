# Point-in-time security metadata contract (Phase 9.22)

## Purpose and status

This contract defines how historical security metadata may enter research
features or post-hoc coverage diagnostics. It does not add new data providers,
read the local lake, backfill CNEquity, or unlock training. The selector and
coverage audit are pure functions; no current model feature consumes their
output yet.

The metadata dimensions under review are:

- immutable listing date, from which listing age can be calculated;
- effective-dated industry level 1/2/3 classification;
- same-session total and A-share float market capitalization.

A field is not considered historical merely because a row contains a column
named as_of_date, trade_date, or announce_date. The source must prove both when
the value was effective and when it first became knowable.

## Current CNEquity evidence assessment

The repository dependency is still pinned to CNEquity 0.21.0. The current local
runtime version reported elsewhere by the project is 0.23.0; this review checked
both public dataset contracts without changing pyproject or the version lock.
For these security metadata datasets, the 0.23.0 contract retains the same
material PIT limitations as 0.21.0.

| Dimension / dataset | Contract evidence | Research decision |
|---|---|---|
| Listing date: instruments | Contains list_date/delist_date, but contract has pit=false and no available_at or revision history for the fact | Candidate immutable event only after source-specific validation and full universe coverage |
| Industry: industry_members | history_mode=snapshot_with_backfill, fetch_semantics=snapshot, pit=false, pit_grade=none | Not accepted as historical PIT |
| Industry: Shenwan interval source | Contains effective start_date, but normalized history drops update_time and has no source-published availability timestamp | Not accepted as historical PIT |
| Market cap: valuation_metrics | history_mode=snapshot_with_backfill, pit=false, pit_grade=none; snapshot fallback is not a historical publication record | Not accepted as strict PIT |
| Share count: share_structure | pit_grade=partial, pit_quality=reconstructed; backup observations may use sweep date because no disclosure date is available | Not accepted as a strict share-count input to derived market cap |

### Important source details

The CNEquity 0.21.0 security master includes list_date, but its contract does
not include an immutable-event verification flag or source publication time.
Its Baostock instrument adapter documents coverage beginning in 2015 and
omission of Beijing listings; EastMoney is used to fill some listing dates.
Therefore list_date is only a candidate for listing-age analysis. It is not
automatically accepted: the future source adapter must prove the date is the
official first-trade/listing event, bind a replayable receipt, and pass
complete coverage against every signal-date PIT-universe symbol. A missing
date fails that metadata field's coverage audit. Delist dates from a current
instrument master must not be used to rewrite a historical membership
decision; the canonical signal-date PIT universe remains authoritative.

The EastMoney industry adapter fetches the current board-constituent report
and writes the caller's as_of_date onto that snapshot. That date label does not
turn a current snapshot into the classification that was knowable on an old
signal date. The Shenwan interval source has effective start dates, but the
normalized interval output does not retain source publication/update
availability. It is therefore insufficient for a strict historical
classification feature, even though an effective-date expansion is possible.

The valuation_metrics dataset is not marked PIT in either checked contract.
Its push2 fallback is an undated live snapshot; the adapter explicitly limits
that fallback to the last closed session. The share_structure dataset is
marked partial/reconstructed PIT, not strict. Its backup path has no source
disclosure date, so announce_date may be the sweep date. Consequently,
multiplying historical close by these share counts would not establish an
unbiased historical market cap. The float_a_shares field is the relevant
A-share-only count where present, but field correctness does not repair the
missing publication-time evidence.

## Canonical observation schema

Each observation is one instrument + metadata field + effective interval +
availability/revision. Required fields:

| Column | Meaning |
|---|---|
| instrument | Exchange-qualified security identifier |
| field | listing_date, industry_l1_code/l2/l3_code, market_cap_total or market_cap_float |
| classification_system | Required for industry: eastmoney or sw; null for non-taxonomy fields |
| value_unit | date, classification_code or CNY, fixed by field registry |
| value_numeric / value_string / value_date | Exactly one typed value according to the field registry |
| effective_from | Inclusive first date the fact applies |
| effective_to | Exclusive end date; null means open-ended where allowed |
| available_at | Timezone-aware ISO-8601 timestamp when the source fact was first knowable |
| evidence_class | strict_pit or immutable_event |
| pit_grade / quality / is_exact | Field-specific evidence quality and exactness |
| source / source_dataset | Identifies the provider and canonical dataset |
| source_revision_id | Exact source revision pinned by the read receipt |
| source_contract_fingerprint | Contract identity for that dataset revision |
| source_receipt_id | Replayable adapter receipt identity |

The interval convention is half-open: [effective_from, effective_to). All
dates are canonical YYYY-MM-DD and all availability timestamps must include a
timezone. Decision timestamps are interpreted in Asia/Shanghai and must fall
on the supplied signal date.

The source identity registry must be derived from replayable receipts and must
also carry validated_evidence_classes. A row cannot self-assert strict PIT or
immutable-event capability. The metadata adapter must explicitly attest that
the source's semantics support the requested evidence class after validating
the pinned dataset contract and its own normalization rules.

## Field-specific policy

- listing_date is an immutable event, not a revisable point estimate. It
  requires evidence_class=immutable_event, pit_grade=event_verified,
  quality=verified and is_exact=true. effective_from must equal value_date,
  and the interval is open-ended. If the upstream source exposes only a date
  and not an exact publication timestamp, availability is conservatively set
  to 23:59:59 Asia/Shanghai on the listing date; the fact cannot be used in
  that day's 16:00 signal.
- industry_l1/l2/l3_code requires strict PIT evidence and an explicit
  classification_system. EastMoney and Shenwan codes remain separate
  namespaces; their values are never merged or ranked by publication time.
  Each classification interval has an effective start and optional exclusive
  end plus a separate available_at timestamp. A classification announced in
  advance is usable only on or after effective_from; a later restatement is
  usable only after that revision's available_at.
- market_cap_total and market_cap_float are expressed in CNY. Share counts are
  not accepted in these fields; any future derivation must separately prove
  same-session share structure, A-share float semantics and publication time.
- market_cap_total and market_cap_float require strict PIT evidence, a finite
  non-negative value, and a bounded effective interval. The effective start
  must equal the signal date: market cap is a same-session observation, not a
  slow-moving classification. It is never forward-filled into later signals.

No fallback is permitted from missing market cap to price/turnover proxies,
industry code to ticker prefixes, listing date to the first locally observed
bar, or historical classification to a current snapshot.

## Selection and coverage rules

select_security_metadata_as_of(...) applies these rules:

1. Validate field typing, timezone-aware availability, quality and exactness.
2. Verify every row's source revision, contract fingerprint and receipt ID
   against the pinned source identity map.
3. Ignore rows whose available_at is later than the Shanghai decision time.
4. Resolve revisions for the same instrument/field/effective_from by the
   latest available_at that was already knowable at the decision time.
5. Select only an interval that contains signal_date. A later effective event
   supersedes a prior open-ended interval. Explicitly overlapping bounded
   intervals are rejected.
6. Enforce same-session-only market cap and return no row for missing facts.
   No imputation or forward-fill occurs.

The selected panel is stamped with signal_date and decision_at. The independent
coverage audit compares the exact Cartesian product of signal-date PIT-universe
instruments and requested metadata fields against selected rows. Any missing
instrument/field pair is FAIL; a high percentage is not treated as complete.

A metadata source cannot be admitted based only on receipt presence or min/max
dates. It also needs dataset-specific expected/observed key reconciliation,
interior-gap checks, source coverage scope, and a documented effective-date
policy. Coverage must include delisted historical symbols that belong to the
signal-date universe, not only today's survivors.

## Walk-forward and training boundary

Phase 9.22 adds the pure validation contract and synthetic tests only. The
current ResearchDataBundle, feature builder, admission function and
walk-forward runner do not consume these fields. Until a source adapter passes
the contract and a matching coverage audit is bound into bundle provenance,
industry and market-cap fields remain unavailable to the model. Listing date
remains conditional on immutable-event verification and complete universe
coverage.

No historical metadata has been downloaded, no local data-lake content was
read, no CNEquity query/backfill was run, and no model fit or market backtest
was performed.

## Source contract references

- [CNEquity 0.21.0 dataset contract](https://github.com/rootSunc/CNEquity/blob/v0.21.0/contracts/v0.21.0.json)
- [CNEquity 0.23.0 dataset contract](https://github.com/rootSunc/CNEquity/blob/v0.23.0/contracts/v0.23.0.json)
- [EastMoney industry adapter](https://github.com/rootSunc/CNEquity/blob/v0.21.0/src/cnequity/adapters/eastmoney/industry.py)
- [Shenwan industry interval adapter](https://github.com/rootSunc/CNEquity/blob/v0.21.0/src/cnequity/adapters/sw/industry_history.py)
- [Instrument basics / listing dates](https://github.com/rootSunc/CNEquity/blob/v0.21.0/src/cnequity/adapters/baostock/instruments.py)
- [EastMoney listing-date enrichment](https://github.com/rootSunc/CNEquity/blob/v0.21.0/src/cnequity/adapters/eastmoney/instruments.py)
- [Valuation adapter semantics](https://github.com/rootSunc/CNEquity/blob/v0.21.0/src/cnequity/adapters/eastmoney/valuation.py)
- [Industry level migration](https://github.com/rootSunc/CNEquity/blob/v0.21.0/contracts/migrations/0.20.0/industry_members.md)
- [A-share float migration](https://github.com/rootSunc/CNEquity/blob/v0.21.0/contracts/migrations/0.21.0/share_structure.md)
