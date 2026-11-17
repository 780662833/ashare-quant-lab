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


## Executable evidence-envelope contract (Phase 9.25)

The repository now includes
`ashare_quant.research.listing_event_evidence.validate_listing_event_evidence(...)`.
This is a pure validator over caller-supplied, already captured evidence rows; it
does not open URLs or call any exchange/vendor service.

Each event row must carry the exchange-qualified instrument, exchange, initial
listing event type, listing date, exchange-specific source-dataset namespace,
document kind and ID, official HTTPS URL, SHA-256 digest of the captured source
document/response, exact record locator, public publication date and precision,
published timestamp, availability timestamp, optional document/signature date,
capture timestamp, BSE Select-Layer transfer flag, and the source revision,
contract fingerprint and receipt ID.

The validator applies these controls:

- SSE rows must use `.SH` identifiers and an SSE official domain; SZSE rows
  must use `.SZ` and an SZSE official domain; BSE rows must use `.BJ` and a
  BSE official domain. Host matching is exact-or-subdomain, so lookalike
  suffixes such as `sse.com.cn.evil.example` are rejected.
- `listing_notice` is accepted for each exchange. `monthly_listing_report`
  is accepted only for SSE; it is supporting evidence, not an assurance of
  complete universe coverage.
- A date-only public release is conservatively normalized to 23:59:59
  Asia/Shanghai on the publication date. `available_at` must equal that
  documented public release time. The document/signature date remains a
  separate field and cannot replace the publication date.
- A precise timestamp must be timezone-aware and consistent with the
  publication date. The evidence capture timestamp cannot precede publication.
- The source document digest must be 64-character SHA-256 hex, the record
  locator and document ID must be non-empty, and the dataset/revision/
  fingerprint/receipt must pass the Phase 9.23 field-scoped capability
  validator.
- All BSE Select-Layer transfer rows are rejected until the source evidence
  proves the legally defined listing-age start separately from the BSE
  code-migration date. No date is inferred from a current code mapping.

The function validates digest shape and records the digest; it does not hash
or download an external document. The adapter/capture layer must compute the
digest over the retained original bytes, preserve those bytes, and keep the
audit evidence independently retrievable. Passing this envelope also does not
prove full PIT-universe completeness or authenticate the upstream reviewer.
The validator is not connected to bundle assembly, feature construction,
training admission or walk-forward execution.

Synthetic tests exercise allowed exchange domains and identifiers, publication
timestamp precision, signature-date separation, digest and record-locator
requirements, receipt mismatch, duplicate events and BSE transfer rejection.
No external endpoint, source record or real market data was accessed.

## Signal-date availability audit (Phase 9.26)

The evidence module now also provides
`audit_listing_event_signal_coverage(...)`. It accepts an exact expected
instrument snapshot, a timezone-aware decision cutoff on one signal date, the
captured event rows, and their receipt-derived source identities. The function
first runs the existing official-document and capability validator, then
checks each expected instrument for:

- a validated initial-listing event;
- an effective listing date no later than the signal date; and
- a documented public-availability timestamp no later than the decision cutoff.

A date-only publication timestamp remains conservatively fixed at
23:59:59 Asia/Shanghai. It cannot qualify for an earlier same-day signal
decision. Missing evidence, a not-yet-public event, or a listing date after the
signal date produces FAIL. Valid evidence for other instruments is reported
separately and does not count as missing membership.

The expected symbol list must come from the exact historical PIT universe
snapshot being audited, not the current instruments table. The function does
not retrieve that universe, prove its completeness, validate delisting history,
or unlock feature construction, training, or backtesting. A PASS is limited to
the supplied membership snapshot and one signal-date decision cutoff. The
weekly research window still requires a separate audit for every rebalance
signal date, plus the historical-universe and delisting gates.

## Public archive recheck (2026-11-16)

This recheck inspected the public search interfaces only. No archive API was
called, no announcement or PDF was downloaded, and no source was promoted.

| Exchange | Confirmed from the public interface | Still unproven |
|---|---|---|
| SSE | The company-announcement page exposes security code/name, keyword, and start/end-date search fields. A per-company announcement view renders security code, short name, title, and announcement date. The current listing-announcement rule requires the issuer's listing information to include the listing time and stock code. | The public page review did not establish a stable, reproducible API, full-page pagination, retention back to the start of the research window, or complete coverage of subsequently delisted securities. The search interface alone does not prove when each exact record first became public. |
| SZSE | The official listed-company notice index exposes keyword and publication-date search and shows listing/termination notices with publication dates. The index is paginated; the reviewed first pages include recent 2026 notices and an older page extending into 2025. | The reviewed pages do not establish the oldest retained record, exhaustive pagination, a stable record identifier for every event, or complete coverage of delisted and code-migrated securities. Publication date is visible, but an exact intraday public-release timestamp was not demonstrated. |

References reviewed:

- SSE company-announcement search interface: https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml
- SSE example per-company announcement view: https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=600035
- SSE 2025 listing-announcement content rule: https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/issue/c/c_20250515_10779011.shtml
- SZSE listed-company notice index: https://www.szse.cn/www/disclosure/notice/company/index.html
- SZSE older paginated notice index: https://www.szse.cn/disclosure/notice/company/index_2.html

**Decision remains unchanged:** SSE issuer listing notices and SZSE listing/termination notices remain official event-level candidates, not production-qualified sources. The next source-research step is to identify and test the exact query/pagination mechanism over bounded historical intervals, prove exhaustive enumeration and stable document identity, and reconcile the resulting event inventory against the accepted historical SH/SZ PIT universe. Do not write a crawler that assumes search-engine result completeness or a current page's visible items represent the full archive.

## Official announcement API evaluation (2026-11-16)

A separate official service channel was identified: Shanghai Stock Exchange Information
Network Co., Ltd. (SSE InfoNet) publishes an **Announcement File Service** product
description. The published service document states:

- the service is delivered through authenticated APIs;
- its stated stock-announcement scope includes SSE main board, SSE STAR Market,
  and SZSE-listed companies (plus separate bond/fund/regulatory modules);
- announcement metadata can be queried by date and includes an index, security
  code, title, category, update time, and download URL;
- the index and download URL can each serve as a unique identifier; and
- the described business flow is application, review, credential provisioning,
  API trial, formal contract, and payment.

This is a materially stronger candidate than inferring archive completeness from
the public web search pages. It is nevertheless **not yet qualified for the
research window**. The public product summary does not establish:

1. whether historical metadata can be queried back to 2018-01-01, or only
   recent/daily updates can be polled;
2. whether records cover securities after delisting and all historical code
   migrations;
3. whether the reported `update time` is the original first-publication time,
   rather than a later update/correction time;
4. whether the service returns an exchange/company announcement publication
   date and, where available, an exact public-release timestamp;
5. stable pagination, correction/deletion semantics, duplicate handling,
   retention SLA, per-market completeness, or revision replay; and
6. trial/production rate limits, API technical documentation, fee schedule,
   and contract/license terms.

The provider publishes a separate official price page and price-list PDF.
The price list reviewed on 2026-11-16 states **CNY 60,000 per year** for the
Announcement File Service. This is the published list price; confirm whether it
is still applicable to the required scope, whether any trial is available, and
whether taxes, implementation, or additional access fees apply. This annual
fee is not justified for the MVP's listing-event evidence requirement without
a successful historical-coverage proof. Do not create credentials, accept a
contract, or incur a fee as part of this repository phase.

There is also a scope discrepancy that must be resolved in writing. The
provider's current product overview describes Announcement File Service as
covering Shanghai-market stock, bond and fund announcements, regulatory
inquiry letters, and issuance/listing announcements. The older 2022 product
description explicitly lists Shenzhen stock announcements as part of the
product scope. Until the provider confirms that SZSE coverage remains included
in the current priced product, do not treat this service as a combined SH/SZ
source.

The current provider product page offers business application and lists service
phone 4008888400 (extension 2-5) and consultation email ywsun@sse.com.cn. Its
application guide says applicants submit an application, pass approval, and
sign a data-service agreement. The older product description mentioned an API
trial before formal contracting; whether that trial path remains available
must be confirmed.

### Source decision

For the frozen SH/SZ MVP, request the API technical documentation and a
time-bounded trial quotation as the first evaluation path. The evaluation must
ask the provider to demonstrate an exhaustive historical export for a bounded
period, including delisted companies and listing/termination events, and to
explain the temporal meaning of every timestamp. A successful trial sample is
not full-window qualification.

The public SSE and SZSE web interfaces remain a no-cost fallback candidate.
Reverse-engineered endpoints reported by third parties are research leads only:
they are not official API specifications, and their parameter names, result
schemas, rate limits, and historical coverage have not been independently
verified in this project. They must not be used as an admitted PIT source or
quietly promoted to a production adapter.

### Additional public-interface observations

- The SZSE company-notice archive exposes static numbered pages. The reviewed
  `index_7.html` page contains notice dates in June-August 2024, while
  individual official delisting notice pages from 2020 are still reachable.
  These facts establish old individual records and some archive pagination, but
  not exhaustive enumeration between 2018 and the present.
- The SSE `上市/退市公告` entry is a distinct official interface. The public
  result reviewed on 2026-11-16 includes bond listing/quotation notices, so
  stock event retrieval requires an explicit security-type filter and
  verification that both stock listing and delisting event classes are covered.
- The 2023 financial-industry announcement electronic specification defines
  announcement-header fields including a unique announcement identifier,
  company/security code, announcement number, and formal publication date.
  This is a useful capture-schema reference, but does not prove that an
  exchange search interface returns the complete fields or original bytes.

### Required decision gate before adapter implementation

Do not select a production collector until one of these paths passes a bounded
proof-of-coverage exercise:

A. **SSE InfoNet trial:** receive the actual technical specification and sample
credentials through the provider's authorized process; prove historical query
semantics, full pagination/export, original publication-time semantics,
delisted-symbol coverage, and exact downloadable-document identity.

B. **Public exchange archive:** independently enumerate all result pages and
event types over the same bounded historical window; retain raw responses and
original documents; reconcile expected versus observed symbols and events;
prove that reruns produce the same record inventory or explain revisions.

Whichever path is selected must map source publication dates/timestamps to the
existing `available_at` rule, retain document bytes and SHA-256 digests, and
produce replayable revision/receipt identities before it can feed
`validate_listing_event_evidence(...)`. The expected universe must still come
from the project's independently qualified historical PIT universe, never from
a current instruments snapshot.

Public references for this recheck:

- SSE InfoNet Announcement File Service product description (published
  2022-10-26; later hosted under the provider's public document service):
  https://bsp.sseinfo.com/admin/static/public/2024-06-07/1fb00bf6492f41fa8aafbadd4c7fe19d/%E4%B8%8A%E8%AF%81%E9%87%91%E8%9E%8D%E6%95%B0%E6%8D%AE%E5%85%AC%E5%91%8A%E6%96%87%E4%BB%B6%E6%9C%8D%E5%8A%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.pdf
- SZSE older numbered company-notice archive page:
  https://www.szse.cn/disclosure/notice/company/index_7.html
- SZSE individual 2020 delisting notice example:
  https://www.szse.cn/disclosure/notice/t20200522_577530.html
- SSE listing/delisting notice entry:
  https://www.sse.com.cn/disclosure/announcement/listing/
- Financial-industry standard JR/T 0021.1-2023, announcement structure and
  header metadata:
  https://www.sse.com.cn/lawandrules/regulations/csrcannoun/c/10751844/files/c231e94e119145c2ba0c44ecd84dc713.pdf
- Provider's current disclosure-data service overview and application/contact
  information: https://www.sseinfo.com/services/data/szjr/
- Provider's current product price page: https://www.sseinfo.com/services/cpfwjg/
- Provider's current official price-list PDF:
  https://www.sseinfo.com/services/charge/pricelist/c/10010718/files/6e4e8d001bbd4554a254aeac11ee0e50.pdf
- Provider's application guide: https://bsp.sseinfo.com/business/?id=1703679027550273537
- Third-party reverse-engineering lead for exchange bulletin APIs (not an
  authoritative specification and not used as a production source):
  https://pipeworx.io/docs/reference/china-exchange-data/

## CNINFO public disclosure archive evaluation (2026-11-16)

CNINFO is a strong no-paid-contract candidate to test before purchasing the
SSE InfoNet Announcement File Service. The current CNINFO homepage describes
itself as the Shenzhen Stock Exchange's official information-disclosure
website, operated by Shenzhen Securities Information Co., Ltd., a wholly owned
SZSE subsidiary. Its current site navigation exposes Shenzhen and Shanghai
markets, Shenzhen Main Board, ChiNext, Shanghai Main Board, STAR Market, and a
`终止/退市` (termination/delisting) information section. This supports broad
market relevance, but the visible navigation is not evidence of complete
historical event coverage.

The CSRC's 2009 announcement formally designated CNINFO among five websites
for ChiNext legal disclosures and said that all ChiNext IPO prospectuses,
listing announcements, interim announcements and periodic reports would be
published free of charge, timely, accurately and completely. That official
decision is explicitly about ChiNext. It must not be extrapolated into a claim
that CNINFO was the sole designated disclosure outlet for all SSE/SZSE boards
or that its query archive is complete from 2018 onward.

### Query protocol: research lead, not official API specification

An independently maintained third-party client currently documents and uses
the following reverse-engineered interface:

- query endpoint: `https://www.cninfo.com.cn/new/hisAnnouncement/query`;
- request method: HTTP POST, form-encoded;
- search page bootstrap: `https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search`;
- request fields include `pageNum`, `pageSize`, `column`, `tabName`,
  `plate`, `stock`, `searchkey`, `category`, and `seDate`;
- response fields observed by that client include `totalpages`,
  `totalRecordNum`, `hasMore`, and announcement rows with
  `secCode`, `secName`, `orgId`, `announcementId`,
  `announcementTitle`, `announcementTime`, `adjunctUrl`, and
  `adjunctType`;
- the associated original file is commonly addressed through
  `https://static.cninfo.com.cn/` plus `adjunctUrl`.

This interface description is derived from third-party code, not a CNINFO
published API contract. Parameter semantics, current availability, rate
limits, pagination stability, field stability, access conditions, and reuse
licensing have not been independently verified here. The official homepage
also exposes a public Data API/data-service section; this repository has not
obtained its technical specification or pricing.

One third-party implementation notes that `announcementTime` commonly
represents 00:00:00 China time for a calendar date. This is a warning that the
field may be a date stamp, not proof of the original intraday publication
instant. Unless CNINFO's source semantics prove a precise original release
timestamp, normalize date-only publication conservatively to
23:59:59 Asia/Shanghai for PIT decisions. Do not set `available_at` to
midnight merely because a JSON field is encoded as a millisecond timestamp.

### Comparison with the SSE InfoNet paid service

| Candidate | Current evidence | Principal unresolved risks |
|---|---|---|
| CNINFO public site / reverse-engineered query | Official disclosure website operated by an SZSE subsidiary; current navigation spans SH/SZ markets and boards; a third-party client demonstrates a POST search pattern and announcement IDs/file URLs. | No official API specification reviewed; query protocol is reverse-engineered; exact publication-time semantics, legal scope by board, full retention, delisted coverage, page exhaustiveness, revisions and bulk-reuse terms are unproven. |
| SSE InfoNet Announcement File Service | Official commercial product; published list price CNY 60,000/year; older 2022 service document includes SZSE announcements, while the current product overview does not expressly confirm SZSE scope. | Cost; current SH/SZ scope discrepancy; historical/delisted coverage, first-publication timestamps, revision replay and trial conditions unproven. |

The recommended order is therefore: qualify the no-cost public CNINFO path with a
small, rate-limited and replayable metadata probe first; only seek a paid
InfoNet trial if the CNINFO path cannot demonstrate the required SH/SZ coverage
and provenance. “Publicly viewable” does not automatically mean unrestricted
bulk redistribution; any production use must respect the applicable website
terms and provider licensing.

### Bounded CNINFO proof-of-coverage plan

Do not ingest CNINFO records into the production metadata datasets yet. The
first probe must be read-only, bounded, and explicitly marked experimental:

1. Bootstrap the official search page and issue one small query for a known
   listing notice and one known termination/delisting notice in each in-scope
   board family (SSE main, SSE STAR, SZSE main, ChiNext). Do not include BSE or
   ETFs in the frozen SH/SZ MVP.
2. Capture the exact request parameters, response bytes, retrieval time,
   response digest, page number, `totalpages`, `totalRecordNum`, and
   `hasMore`. Keep the raw response; do not store only parsed rows.
3. Verify that every page in the requested bounded interval is retrievable,
   that total counts remain consistent across pages, and that no page repeats
   or silently disappears. If the page cap is reached, label the probe
   PARTIAL; never infer completeness from the first page.
4. Verify the identity join among exchange-qualified symbol, company name,
   announcement ID, announcement title, and original file URL. Preserve the
   original downloaded document bytes and SHA-256 for the small set of
   qualification samples; do not treat a result-row ID alone as proof that
   the underlying document is immutable.
5. Compare publication date/time with the original disclosure page/document
   and determine whether the source exposes first-publication time or only a
   date/update timestamp. Revisions, replacements, duplicate notices and
   corrected announcements must remain distinguishable.
6. Reconcile listing and termination events across a bounded historical slice
   against independently enumerated exchange event inventories. Explicitly
   include delisted issuers and exceptional code changes. Only after the
   bounded proof succeeds should the team expand the interval toward
   2018-01-01 and compare against the accepted historical PIT universe.

A passing metadata probe is not a production source qualification. The full
historical expected universe still must come from an independently accepted
PIT universe source, and each accepted listing event must pass the existing
receipt-bound immutable-event evidence validator and signal-date availability
audit. No production adapter, training feature, or backtest input is enabled
by this research phase.

### Public references

- CNINFO current homepage and its description of official status, operator,
  market sections and termination/delisting navigation:
  https://www.cninfo.com.cn/
- CSRC 2009 formal announcement designating CNINFO among the ChiNext disclosure
  websites:
  https://www.csrc.gov.cn/csrc/c100028/c1002699/content.shtml
- Third-party reverse-engineered CNINFO query client (research lead only):
  https://github.com/Hsiangpo/cninfo-/blob/main/src/cninfo_ssgs/cninfo_api.py
- Additional third-party protocol example (not an authoritative specification):
  https://github.com/gaodechen/cninfo_process/blob/master/spider.py

## Experimental metadata probe tool (Phase 9.29)

The repository now contains
`scripts/probe_cninfo_announcement_api.py` and
`ashare_quant.research.cninfo_probe.probe_cninfo_announcement_api(...)`.
This is a bounded research probe for the reverse-engineered CNINFO search
route, not a registered provider adapter.

The CLI is deliberately **no-network by default**. For example, this first
command only prints the request plan:

```text
python scripts/probe_cninfo_announcement_api.py --start-date 2026-10-12 --end-date 2026-10-12 --column szse --plate szcy --keyword "上市公告书"
```

To make the bounded request, the operator must explicitly append `--execute`:

```text
python scripts/probe_cninfo_announcement_api.py --start-date 2026-10-12 --end-date 2026-10-12 --column szse --plate szcy --keyword "上市公告书" --execute
```

A second example uses a known SZSE delisting-notice date as a search lead; it
must be treated as a protocol check, not a guarantee that the reverse-engineered
keyword returns the document:

```text
python scripts/probe_cninfo_announcement_api.py --start-date 2020-05-22 --end-date 2020-05-22 --column szse --plate sz --keyword "终止上市" --execute
```

The tool enforces a maximum 31-calendar-day date window, a page cap of 10
(default 2), page size 1-100 (default 30), and a minimum 0.5-second interval
between network requests (default 1 second). It bootstraps the search page,
submits one form-encoded POST per captured result page, and writes only to
`reports/source_probes/cninfo/` (or the caller-selected output directory).
It stores the bootstrap response, raw JSON response pages, request parameters,
request/capture timestamps, request-body/response byte counts and SHA-256 hashes, server page
and record counters, duplicate announcement IDs, and missing identity fields.
It does not download announcement PDFs, call CNEquity, read or write
`F:\cnequity`, backfill data, train a model, or run a backtest.

Probe statuses have deliberately narrow meanings:

- `COMPLETE_FOR_QUERY`: every page reported for this one query was captured
  and no integrity warning was observed. This is not source-wide archive
  completeness.
- `COMPLETE_WITH_WARNINGS`: all query pages were traversed, but duplicate IDs,
  missing identity fields or other query-counter anomalies were observed.
- `PARTIAL`: the server reports more pages than the configured cap, a page
  disappeared, or page/record counters are inconsistent.
- `NO_RESULTS`: the query returned no rows.
- `ERROR`: the bootstrap, request, response schema or transport failed.

Every report permanently leaves
`source_coverage_proven=false`, `pit_admitted=false`,
`training_unlocked=false`, and `backtest_admitted=false`. Raw response hashes
are capture hashes only; they do not constitute CNINFO revision receipts or
prove that an announcement PDF is immutable. The announcement-time observation
is informational only: midnight-valued timestamps are marked
`midnight_date_stamp_suspected`, and non-midnight values remain
`intraday_value_unverified`.

This script has not been run against CNINFO by the project CI environment.
Synthetic unit tests exercise request planning, bounded pagination, raw capture,
hashing, partial status, duplicate IDs, no-result behavior, and missing
identity fields. A local execution result is still required before we can claim
that the endpoint works with the current service.

## Live CNINFO probe observation and pagination counter correction (2026-11-16)

The first user-run live probe completed both the official search-page bootstrap
and the reverse-engineered query POST with HTTP 200. The bounded query was:

- board selector: `column=szse`, `plate=sz`;
- date interval: 2020-05-22 through 2020-05-22;
- keyword: `终止上市`;
- requested page size: 30; maximum pages: 1.

The returned page contained 5 announcement rows, 5 unique
`announcementId` values, and no missing required identity fields. The query
reported `totalRecordNum=5` and `hasMore=false`, but also
`totalpages=0`. All five `announcementTime` values were classified by the
probe as midnight-valued date stamps; none is accepted as an exact intraday
first-publication time.

The initial probe version returned `PARTIAL` because it treated the zero
`totalpages` counter as a hard pagination failure. The current parser change
preserves that raw counter while deriving an effective one-page expectation
from `totalRecordNum/pageSize`. When the captured row count equals
the reported total, all five IDs are unique, no required identity field is
missing, and `hasMore=false`, the revised status is
`COMPLETE_WITH_WARNINGS`: the one query page appears completely traversed,
but the server's page counter remains anomalous. A `hasMore=true`, record-count
mismatch, changed counter, duplicate ID or identity-field gap remains
PARTIAL or warning-bearing as appropriate.

This observation demonstrates that the current CNINFO public search route is
reachable from the operator's network and returns announcement metadata for
this one query. It does not yet prove that the five rows are all relevant
listing/termination events for the issuer set, that the raw field timestamps
are original first-publication times, or that the 2020 archive is complete.
The raw response page must be reviewed before expanding query scope.

