# Listing-date source qualification (Phase 9.24)

## Decision and status

**No listing-date source is promoted to production by this phase.** The public review found useful official event records and a third-party full-universe candidate, but did not establish a single source that simultaneously proves:

1. complete coverage of the signal-date A-share universe from the beginning of the research window, including subsequently delisted securities and historical code migrations;
2. the official listing event and its effective date for each exchange-qualified instrument;
3. when that exact evidence first became publicly available;
4. row-level provenance that can be replayed and pinned to a dataset revision, contract fingerprint and read receipt.

This phase documents the candidate-source decision and reserves exchange-specific dataset names in the immutable listing-date field contract. It does not query any API, download a dataset, verify real row counts, accept a vendor, read the local data lake, or enable a listing-age model feature.

Review date: 2026-11-14.

## Field semantics and temporal rule

For the current research contract, `listing_date` is an immutable listing event:

- `effective_from` and `value_date` equal the qualified official listing-age start date;
- `effective_to` is open-ended;
- `available_at` is the first public availability time of the exact evidence record used by the adapter, not the date printed in the document by assumption and not automatically the listing date;
- if the official public release is only date-stamped, set `available_at` conservatively to 23:59:59 Asia/Shanghai on that publication date. A signal earlier that same day must not use the record;
- retain the document publication date separately from its signature/document date. When those differ, the publication event controls availability;
- if the source only offers a current security master and cannot reproduce when a row/version first became available, it cannot independently prove historical availability.

The first trading/listing event, the first date an instrument enters the project's validated A-share universe, and a later code-migration date are not interchangeable. The canonical signal-date universe remains authoritative for membership. A metadata source must not backfill a symbol into the universe simply because a modern security master contains it.

### Beijing Stock Exchange migration edge case

The BSE states it was registered on 2021-09-03 and began trading on 2021-11-15. Its initial listing rules say that companies transferred from the NEEQ Select Layer retain the listing-time calculation from their Select Layer listing date. Therefore a BSE migration/transfer date, a new-code effective date and the legal listing-age start may differ. The BSE old/new-code mapping page is useful for identifier reconciliation, but its displayed `上市日期` must not be assumed to be the original legal listing-age start without checking the event definition and source document. Until that mapping is resolved for each migrated security, the BSE migrated-symbol listing-date row remains unqualified.

The present metadata field remains one immutable event value; do not silently choose between those dates. A future adapter must either prove the field's defined listing-age semantics for the migrated symbol or fail closed for that row.

## Source review

| Candidate | What the public source demonstrates | What it does not yet demonstrate | Decision |
|---|---|---|---|
| SSE monthly statistics reports | The SSE says the monthly report is disclosed on the 15th of each month. The current archive index visibly lists monthly reports from 2020-10 through 2021-12. A December 2021 report has a `Securities Listed` table with security code, short name and listing date. | The displayed archive index is not proof of complete 1990-to-present coverage. The table is a period listing table, not a full current-and-delisted security master. The monthly report publication date is later than some listing events and is not necessarily the first publication of the event. | Official corroboration / delayed completeness cross-check only; not a standalone full-history source. |
| SSE listing and delisting announcement pages | SSE has a securities list and a separate suspended/terminated-listing area. Individual delisting notices identify the security code and distinguish the decision, delisting-period end and final delisting date. | The public dynamic pages reviewed do not prove a complete, stable, machine-readable historical event archive for every listing since 1990. A current page or current downloadable list is not historical universe evidence. | Official event evidence candidate; archive retention and complete pagination must be proven before use. |
| SZSE company announcement archive | The SZSE company notice feed contains both stock-listing and termination-listing announcements, with a publication-date field and dated archive pages. An example notice published on 2026-10-12 states that code 301660 would list on 2026-10-13; the document's signature date is 2026-10-09. | The search-visible archive pages establish recent event examples, not complete historical retention or a complete historical symbol inventory. The difference between the page publication date and document date demonstrates why availability cannot be inferred from the signature date alone. | Best official candidate for per-event evidence; exact release timestamp and complete historical archive still need qualification. |
| BSE announcement archive and old/new code mapping | BSE official materials establish the exchange opening date, provide old/new code mapping examples with a listing-date column, and publish individual listing/termination notices. | The mapping page is not a full historical symbol master. Select-Layer migration creates a distinct listing-age semantic. The public pages reviewed do not establish complete per-symbol coverage or first-publication timestamps. | Official event evidence and code reconciliation candidate; migrated-code rows remain blocked pending semantic mapping. |
| CnOpenData A-share basic-information API | Its public API description claims coverage of Shanghai, Shenzhen and Beijing A-shares, listing dates back to 1990-12, and records for delisted securities. It says the data is refreshed daily after the trading close and requires an API key. | The public contract does not expose row-level official document links, original publication timestamps, immutable source revisions or replayable per-row source receipts. A daily refreshed current master is not by itself a historical point-in-time evidence archive. The claimed coverage has not been independently tested. | Candidate for a later completeness cross-check or seed inventory only. Not admitted as immutable-event evidence. |
| CNEquity `instruments` | The existing project review found `list_date`/ `delist_date` fields. | Its reviewed dataset contract is not PIT and has no source publication-time or revision semantics for those facts; adapter coverage limitations also remain. | Retain as a conditional candidate only; do not claim it passes source qualification. |

## Required production evidence envelope

Every listing-date observation admitted from an exchange event adapter must be traceable to a specific source document and exact row/record. The independently retained evidence record must include at least:

- exchange and exchange-qualified instrument identifier;
- official event type and the defined listing-age start date;
- source document ID / announcement number and canonical HTTPS URL;
- official publication timestamp (timezone-aware), plus the printed document date when different;
- stable record locator (page/table/row or announcement section);
- raw document or response digest (SHA-256), capture timestamp and replayable receipt ID;
- dataset revision ID and contract fingerprint pinned by the receipt;
- a field-semantic immutable-event capability audit ID whose result is independently retained.

For PDF documents, preserve the original PDF bytes and digest, page number and row locator. For HTML announcements, preserve the original response bytes or a replayable immutable capture, announcement ID, canonical page URL and publication metadata. A title, search-engine snippet, current company profile or unversioned CSV export is not sufficient evidence on its own.

The adapter's `available_at` must be derived from the source's documented public-release event. A document footer date is not automatically its web publication date. If only a publication date is retrievable, use that date at 23:59:59 Asia/Shanghai. Never stamp the listing date itself as `available_at` unless the source actually proves that the record was public by that timestamp.

The Phase 9.23 `build_security_metadata_source_identity(...)` helper binds the structural capability audit to a replayable read receipt and field/dataset identity. It does not authenticate the auditor or independently validate the original source document. Production audit IDs must refer to retained review material outside a caller-supplied synthetic mapping.

## Acceptance gates before feature integration

A source cannot be promoted based on a small successful sample. The complete acceptance sequence is:

1. **Universe reconciliation:** compare the source's symbol inventory to every symbol in the canonical PIT A-share universe used by the project across the full research window. Include securities later delisted, suspended, transferred, renamed or code-migrated. Do not derive the expected universe from today's listed companies.
2. **Exact coverage:** require one valid listing-date record for every expected instrument/field pair. Missing or conflicting rows fail the entire requested source window; do not patch gaps with a current master or infer dates from the first observed daily bar.
3. **Exchange and era stratification:** report expected/observed/missing/conflicting counts by SSE/SZSE/BSE and listing year. Explicitly reconcile old/new BSE codes and all historical alias relationships.
4. **Primary-source verification:** verify every exceptional row (delisted, code-migrated, relisted, cross-board or ambiguous) against the exchange's original event record. Perform stratified samples across older and newer listing cohorts for ordinary rows. Store the evidence document digests and locators.
5. **Availability replay:** replay the exact source receipt and verify that the evidence record's `available_at` is no earlier than the original public-release timestamp and no later than the decision cutoff at which it is selected.
6. **Conflict handling:** fail if official documents disagree on instrument identity or listing-age start date. Do not resolve conflicts by choosing the latest date or whichever source matches the daily-bar history.
7. **Capability and coverage audit:** issue a unique, independently retained immutable-event audit ID tied to the exact dataset revision/receipt, then pass the existing full signal-universe coverage audit.

The metadata field may be integrated only after all gates pass on the accepted PIT universe and the same receipts are included in bundle provenance. Until then, listing date stays out of the model feature builder, training admission and walk-forward runner.

## Implementation decision

- The contract now permits the exchange-specific source dataset names `sse_listing_events`, `szse_listing_events` and `bse_listing_events` for the immutable `listing_date` field. This only opens a schema path for future receipt-bound adapters; it does not register a live adapter or certify any of the public sources.
- Use original exchange listing notices as the event-level primary evidence where their archives can be proven complete and replayable.
- Use exchange monthly reports and a qualified third-party inventory as independent completeness cross-checks, not substitutes for row-level event provenance.
- Keep CnOpenData and CNEquity out of production listing-date features until a source receipt, row-level evidence envelope, full PIT-universe reconciliation and independent capability audit are demonstrated.
- Do not alter the frozen MVP, the CNEquity version lock or the current research window.

## Public source references reviewed

- SSE monthly statistics page: https://www.sse.com.cn/aboutus/publication/monthly/company/
- SSE monthly-report archive and its publication cadence: https://www.sse.com.cn/aboutus/publication/monthly/documents/
- SSE December 2021 monthly report (see `Securities Listed` table): https://www.sse.com.cn/aboutus/publication/monthly/documents/c/10061118/files/2e31b357ff4641f1956279cd7decc195.pdf
- SSE suspended / terminated-listing area: https://www.sse.com.cn/assortment/stock/list/delisting/
- Example SSE delisting notice (code, termination and delisting dates): https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20260629_10823832.shtml
- SZSE company listing / termination notice archive: https://www.szse.cn/www/disclosure/notice/company/index.html
- Example SZSE listing notice (page publication date and next-day listing date): https://www.szse.cn/disclosure/notice/t20261012_623256.html
- BSE overview: https://www.bse.cn/company/introduce.html
- BSE initial listing rule, including Select-Layer transfer continuity: https://www.bse.cn/cxjg_list/200010908.html
- BSE old/new code mapping: https://www.bse.cn/service/code_mapping.html
- CnOpenData A-share basic-information API description: https://v2026-sso.cnopendata.com/api-store/ashare-basic-api

No source API was invoked and no data rows were downloaded during this review.
