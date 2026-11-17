# Delisting-event source qualification (Phase 9.34)

## Decision and status

**The reverse-engineered CNINFO historical search route is not qualified as the discovery source for historical SZSE delisting decisions.** For SZSE termination decisions, use the exchange-issued notice in the SZSE "本所公告" archive as the primary event evidence, subject to a separate archive-completeness and replayability audit.

This is a source-selection decision, not production admission. A successfully opened official page proves one event record exists; it does not prove the archive is exhaustive, stable, or historically complete. No production adapter, PIT availability, historical-universe completeness, training, or backtest gate is admitted by this phase.

Review date: 2026-11-16.

## 002604 case study

### Primary exchange record

The SZSE published **《关于山东龙力生物科技股份有限公司股票终止上市的公告》** on 2020-05-22:

- Official page: https://www.szse.cn/disclosure/notice/t20200522_577530.html
- Page record key: `t20200522_577530`
- Exchange: SZSE
- Instrument: `002604.SZ`
- Issuer: 山东龙力生物科技股份有限公司
- Security name as stated in the decision: `*ST龙力`
- Event: SZSE decided to terminate the listing on 2020-05-22.
- The notice says the shares would enter the delisting consolidation period on 2020-06-01, lasting 30 trading days, with delisting after that period.

The page is under the exchange's **本所公告** route (`/disclosure/notice/`). It is an exchange-issued decision, not an issuer-authored disclosure. The official page states the decision date and the subsequent consolidation-period start. It does not, by itself, justify collapsing those dates into one generic `delist_date`.

### Independent issuer corroboration

The issuer's notice published on 2020-05-23 states that it received the SZSE termination decision on 2020-05-22, identifies code 002604 and the then security name `*ST龙力`, and cites decision number 深证上〔2020〕428号:

- Official SZSE-hosted PDF: https://disc.static.szse.cn/download/disc/disk02/finalpage/2020-05-23/67606e4a-44b2-4af0-8c1f-595b1a0945b6.PDF
- Issuer notice number: 2020-025
- Publication date: 2020-05-23
- Decision receipt date stated by issuer: 2020-05-22

This issuer-authored notice is corroboration. It is not the exchange decision itself and its 2020-05-23 publication date must not overwrite the exchange decision's 2020-05-22 publication date.

### CNINFO query results and interpretation

The bounded operator probes of `/new/hisAnnouncement/query` tried materially different request forms for the same date:

| Probe | Relevant request variation | Result |
|---|---|---|
| Keyword search | `searchkey=龙力`, `plate=sz` | 0 rows |
| Security code only | `stock=002604`, `plate=sz` | 0 rows |
| Code plus current orgId | `stock=002604,9900021073`, `plate=sz` | 0 rows |
| Code plus current orgId and historical board filter | `stock=002604,9900021073`, `plate=szzx;` | 0 rows |

All four query responses were 165 bytes and had the same response SHA-256:

`c2a890bbf3a6a53ab02ddc6c1794bf1c72ba45799fe9f59dcc5ba2cc18467114`

Each response reported `totalRecordNum=0`, `totalAnnouncement=0`, `totalSecurities=0`, `totalpages=0`, `announcements=null` and `hasMore=false`. The request-body hashes differed, so the requests were not identical.

This repeated identical empty body across different filters is a warning that the reverse-engineered search path is not providing discriminating evidence for this historical case. It does **not** establish whether the underlying cause is search-index coverage, a hidden session/query prerequisite, a request-schema mismatch, or another server-side behavior. The probe's `NO_RESULTS` means only that the server returned an empty response for that request; it is not proof that the announcement is absent from CNINFO's static archive or SZSE's archive.

The CNINFO top-search identity lookup returned:

- `code=002604`
- `orgId=9900021073`
- `zwjc=龙力退`
- `delisted=true`

This is the current search-index identity returned by that endpoint. It does not independently establish the historical 2020 organization identifier or alias. Do not treat today's abbreviated name `龙力退` as the 2020 security name, and do not use the current `orgId` as a historical identity without historical source evidence.

### Do not conflate the exchange's notice collections

The following SZSE collections have different roles:

1. `/disclosure/notice/` — 本所公告. The 002604 termination decision is in this collection and is the primary source for the exchange's decision event.
2. `/www/disclosure/notice/company/` — 上市公司公告. This is the issuer-authored disclosure collection. It can corroborate what the issuer disclosed and when.
3. `/marketServices/deal/period/delisted/` (also exposed under `/www/marketServices/deal/period/delisted/`) — 已退市公司公告. The visible items reviewed are post-delisting announcements such as share-confirmation/transfer notices; this page is not a substitute for the exchange's original termination-decision archive.

Do not scrape or qualify one collection and silently assume it covers the others.

## Event semantics: keep dates separate

The 002604 record requires separate event fields. At minimum, a future event adapter should distinguish:

| Field | 002604 case | Meaning |
|---|---|---|
| `termination_decision_date` | 2020-05-22 | Date the exchange decided to terminate listing |
| `delisting_period_start_date` | 2020-06-01 | First date of the 30-trading-day delisting consolidation period |
| `delisting_effective_date` | Not set from this page alone | Date on which the exchange actually delisted/removed the security after the consolidation period |
| `source_publication_date` | 2020-05-22 | Publication date printed on the exchange page |
| `available_at` | Conservative date-only cutoff: 2020-05-22 23:59:59 Asia/Shanghai | Earliest safe same-day availability bound when no exact release time is established |
| `issuer_notice_publication_date` | 2020-05-23 | Separate corroborating issuer announcement date |

The decision date, start of the delisting consolidation period, final delisting-effective date, last trading date, and issuer notice date are not interchangeable. Do not infer the final delisting-effective date from "30 trading days" without an official terminal notice and the applicable exchange calendar.

The public SZSE page displays a date, not an exact intraday release timestamp. Until the exact publication timestamp is established, retain date precision and conservatively set `available_at` to 23:59:59 Asia/Shanghai on 2020-05-22. A signal earlier on that same date must not use the decision.

## Source hierarchy and adapter policy

For SZSE delisting decision events:

1. **Primary:** original exchange-issued SZSE notice in the `/disclosure/notice/` collection. Preserve the page URL, record key, original response bytes, digest, page publication date and exact evidence locator.
2. **Corroboration:** issuer-authored disclosure and its original document, with its own publication timestamp and document number.
3. **Document mirror:** CNINFO/static PDF may be retained as a secondary document copy only after its title, date, instrument, decision number and body are reconciled to the exchange record. A PDF filename or URL suffix is not automatically the API's announcement ID.
4. **Discovery fallback:** CNINFO's reverse-engineered `hisAnnouncement/query` route remains a research-only lead. It is not a qualified historical event-discovery source for this case and its empty response must not veto a known official exchange record.

A future SZSE adapter must capture raw source bytes, compute SHA-256, retain a stable page/document locator and replayable receipt, derive publication availability from the exchange page's publication metadata, and preserve the original HTML/PDF. For each record, the evidence envelope should include exchange, exchange-qualified instrument, event type, event date, source record ID, canonical URL, publication date and precision, `available_at`, capture timestamp, digest, record locator, and source revision/receipt identity.

## Production acceptance gates

This 002604 case is a positive control, not a completeness test. Before any SZSE event dataset can be admitted:

1. Establish a supported archive-discovery method for `/disclosure/notice/` (documented interface or a reproducible, bounded archive traversal); retain request and page receipts.
2. Reconcile the full target-period archive against the canonical historical SZSE security universe, including subsequently delisted issuers, renames and code/identity changes.
3. Capture all candidate notices and originals. Prove page/record traversal completeness using server counters and independent archive-boundary checks; a complete search response alone is insufficient.
4. Classify risk warnings, proposed actions, issuer notices, exchange decisions, consolidation-period notices and final delisting notices as different event types. Require document review for decision candidates.
5. Reconcile exceptional records to original exchange documents and cross-check with issuer notices where available.
6. Retain first-publication time semantics, immutable source bytes/digests, exact row/section locators and replayable receipts. A date-only page receives the conservative end-of-day availability cutoff.
7. Run the full historical universe, temporal availability, duplicate/conflict and missing-event audits. Any unexplained gap or conflicting event identity fails the requested window.

Until all gates pass, this source remains a verified official case record and a preferred source candidate, **not a production-qualified historical delisting dataset**.

## Public source references

- SZSE exchange-issued 002604 termination decision (2020-05-22): https://www.szse.cn/disclosure/notice/t20200522_577530.html
- SZSE issuer-authored corroborating notice (2020-05-23): https://disc.static.szse.cn/download/disc/disk02/finalpage/2020-05-23/67606e4a-44b2-4af0-8c1f-595b1a0945b6.PDF
- SZSE 本所公告 archive: https://www.szse.cn/disclosure/notice/
- SZSE 上市公司公告 archive: https://www.szse.cn/www/disclosure/notice/company/index.html
- SZSE 已退市公司公告 collection: https://www.szse.cn/marketServices/deal/period/delisted/

No production adapter, lake access, CNEquity read/backfill, training, or real backtest was performed during this qualification review.
