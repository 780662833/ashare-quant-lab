# SZSE route-context investigation (2026-11-20)

## Status

The three previously reported non-reciprocal relations are **not confirmed SZSE source-data errors**. The original capture compared a neighbor API response for one exact URL route with a different captured URL route sharing the same docId.

The two alternate routes are now covered by the bounded capture utility added in PR #111. Their live responses have **not** yet been captured in this execution environment. The graph remains unqualified for full reciprocity acceptance.

The v6 duplicate-count conclusion remains closed and is not changed by this investigation.

## Input evidence

Input archive: szse_year_boundary_audit.zip

SHA-256:

    19067beecb0d8050eeaa2e1205a6dc8fffd5d686d6459d8280ecf85946600d5e

Offline inspection confirms:

- 342 ZIP entries.
- manifest.json contains 198 successful GET request records: 66 HTML pages, 66 same-path JSON records and 66 previous/next API responses.
- The two exact alternate URLs listed below are absent from those 198 records.
- All 66 captured HTML pages and JSON detail records passed the original docId identity check.
- No claim is made that all possible URL routes for those 66 docId values were captured.

## Source API records

The following original neighbor responses explicitly point to these exact target routes:

| Source document | chnlDocId | Relation | Target docId | Exact target URL |
|---|---|---|---|---|
| 584082 | 211_584082 | preDocId | 584083 | /disclosure/notice/t20210104_584083.html |
| 584084 | 211_584084 | nextDocId | 584083 | /disclosure/notice/t20210104_584083.html |
| 584068 | 214_584068 | preDocId | 584085 | /disclosure/notice/company/t20210105_584085.html |

The captured route variants are different:

| docId | Captured URL | HTML navigation channel | JSON data channel |
|---|---|---|---|
| 584083 | /disclosure/notice/general/t20210104_584083.html | 212 / general_news | 212 / general_news |
| 584085 | /disclosure/notice/t20210105_584085.html | 211 / announcement | 214 / ipocompany_news |

The original capture therefore contains only these two route variants, not the alternate target URLs returned by the source API.

The original HTML itself exposes articleDetail.articleId, articleDetail.channelId and articleDetail.channelCode. The route capture utility reads the navigation fields from the exact HTML page. It does not substitute JSON data.channelId.

## Why the earlier reciprocity result is inconclusive

The neighbor response stored for the captured 584083 /general/ route reports preDocId=584135 and nextDocId=618392. This is evidence for that captured route and its navigation context only. It does not establish the previous/next values of the uncaptured /disclosure/notice/t20210104_584083.html route.

The neighbor response stored for the captured 584085 plain /disclosure/notice/ route reports preDocId=584092 and nextDocId=584086. It does not establish the values for the uncaptured /disclosure/notice/company/t20210105_584085.html route.

docId equality alone is not enough to declare two routes aliases. The alternate route must independently pass HTML and JSON identity checks, and its JSON title, publication time and content hash must be compared with the existing capture.

Until those checks are complete, retain the three relationships as:

    unresolved_route_context

Do not create inferred reverse edges. Do not classify a route-context mismatch as a confirmed source-data error.

## Exact next capture

From a local checkout of the merged main branch:

    python scripts/capture_szse_route_context.py --reference-dir reports/szse_year_boundary_audit --output-dir reports/source_probes/szse_route_context --minimum-interval-seconds 1.0 --execute

The reference directory must contain the original extracted pages/<docId>/announcement.json files. The probe makes at most six sequential GET requests:

1. HTML: https://www.szse.cn/disclosure/notice/t20210104_584083.html
2. JSON: https://www.szse.cn/disclosure/notice/t20210104_584083.json
3. Previous/next API for 584083, with the navigation channelId extracted from that exact HTML.
4. HTML: https://www.szse.cn/disclosure/notice/company/t20210105_584085.html
5. JSON: https://www.szse.cn/disclosure/notice/company/t20210105_584085.json
6. Previous/next API for 584085, with the navigation channelId extracted from that exact HTML.

The output report is:

    reports/source_probes/szse_route_context/route_context_report.json

Each successful response is saved as original bytes with SHA-256 and request metadata. The probe is GET-only, exact-host HTTPS allowlisted, rate-limited, bounded to ten routes and 20 MiB per response. PDFs and other attachments are not downloaded.

## Required acceptance checks

The route-specific reverse checks are:

| Exact target route | Required reverse field | Required value |
|---|---|---|
| 584083 plain /notice/ route | nextDocId | 584082 |
| 584083 plain /notice/ route | preDocId | 584084 |
| 584085 /company/ route | nextDocId | 584068 |

Also compare each alternate route against the existing capture on:

- HTML articleId versus URL docId;
- JSON data.docId versus URL docId;
- title;
- publication time;
- exact JSON content SHA-256.

A missing response, HTTP error, chnlDocId mismatch or missing channel context remains unresolved. A matching reverse relation plus a matching business payload supports a route-alias interpretation, but does not by itself establish historical archive completeness.

## Operator-run local exact-route capture result

The operator ran the bounded probe from the local Windows checkout on 2026-11-20. The supplied report records:

- Capture time: 2026-11-20T13:54:20+00:00.
- Status: CAPTURED_BOUNDED_ROUTE_SAMPLE.
- 2 exact routes, 6 sequential GET requests, 1.0-second minimum interval.
- All six responses returned HTTP 200; request_errors=0.
- Both routes passed HTML/JSON URL identity checks.
- All three route-specific reverse checks matched; no mismatch or unresolved check.
- Both route JSON payloads matched their original archived business payloads on docId, title, publication time and exact content SHA-256.

### Exact route evidence

| docId | Exact captured route | HTML navigation context | JSON data context | Neighbor context | Reference comparison |
|---|---|---|---|---|---|
| 584083 | /disclosure/notice/t20210104_584083.html | 211 / announcement | 212 / general_news | chnlDocId=211_584083; pre=584084; next=584082 | same_business_payload; docId/title/pubTime/content SHA-256 all match |
| 584085 | /disclosure/notice/company/t20210105_584085.html | 214 / ipocompany_news | 214 / ipocompany_news | chnlDocId=214_584085; pre=584086; next=584068 | same_business_payload; docId/title/pubTime/content SHA-256 all match |

### Business-payload identity checks

| docId | Title | pub_time (epoch ms) | Reference / route data channel | Reference and route content SHA-256 | Result |
|---|---|---:|---|---|---|
| 584083 | 深圳证券交易所《纪律处分事先告知书》送达公告 | 1609689600000 | 212 / 212 | 358ed2fe4d92aca34ac5a32b08359524882aca9b154a964058fb9ad3fe13e9e4 | docId, title, pub_time and content hash match |
| 584085 | 关于祖名豆制品股份有限公司股票上市交易的公告 | 1609776000000 | 214 / 214 | 081ae9882490417b1e8aa5a3729cb04fc1e7c1664503bf3c81a1e7ed9062fcb0 | docId, title, pub_time and content hash match |

Both pages also report HTML articleId equal to the URL docId; the report records route_identity_status=html_json_doc_id_match for each route.

For 584083, the HTML navigation channel is 211 while the JSON business-data channel is 212. The neighbor API response uses 211, matching the exact HTML navigation context. This is direct evidence that the neighbor request must be scoped by the HTML route's navigation channel, not by the JSON data channel.

For 584085, the exact /company/ route exposes navigation channel 214, and its neighbor response uses chnlDocId=214_584085. The older plain /notice/ capture exposed a different navigation context (211), so it could not stand in for the exact company route.

### Reverse-edge results

| Source relation | Expected reverse field on target route | Observed | Result |
|---|---|---|---|
| 584082 preDocId=584083 | 584083 nextDocId | 584082 | MATCH |
| 584084 nextDocId=584083 | 584083 preDocId | 584084 | MATCH |
| 584068 preDocId=584085 | 584085 nextDocId | 584068 | MATCH |

Thus, the three previously apparent non-reciprocal findings are resolved as route-context mismatches in the original audit sample. They are not confirmed SZSE source-data errors. Do not retain these three checks as unresolved_route_context, and do not create inferred edges: the target-route responses directly contain the matching values.

### Captured response receipts

The operator report records these response SHA-256 values:

| Route | Kind | HTTP | Bytes | Response SHA-256 |
|---|---|---:|---:|---|
| 584083 | HTML | 200 | 18,499 | 48f50a29bb161838ae8f0cb89d24e4561750d73990fdb01af7b191c654206228 |
| 584083 | JSON | 200 | 7,764 | 71939f35413e2ebba4f2999ac3df4e5e0c7d82c168be28f39fcc381a3ff7e9b0 |
| 584083 | neighbor | 200 | 577 | 9cc827507824e8314ee3ad6833be8425072c3288beef267522fde89b565ce183 |
| 584085 | HTML | 200 | 12,951 | 6b2f1ead31d7add88551e229bee2ff427b978c61e9deff2d24c7ecffc57795ad |
| 584085 | JSON | 200 | 2,061 | e3f653d7eacf2e2a0557f3e73687a33f4bbfddf72fe7f9be09781bf7b54e879e |
| 584085 | neighbor | 200 | 513 | 371b0291b8019a6253894a8ed55b6613b168c8e55aef7c321e31ecccc22b0848 |

The report path supplied by the operator is:

    reports/source_probes/szse_route_context/route_context_report.json

The report content was supplied in the conversation; the repository documentation records its reported measurements and response digests. It does not claim that the report JSON or raw response files have been committed to the repository.

### Scope and acceptance boundary

The probe was GET-only, HTTPS-only and restricted to www.szse.cn. It downloaded no attachments. Its safety block explicitly leaves production, PIT, training and backtest admission false, and reports source_error_confirmed_by_tool=false.

This resolves only the three route-specific reciprocity checks and the identity/payload checks for these two routes. The report explicitly returns full_graph_acceptance=false. It does not prove historical archive completeness, full-graph edge reciprocity, global ID uniqueness, chronology, URL/attachment safety across the graph, or historical chain continuity.

## Related anomaly kept separate

The 618392 capture has a URL path date segment 2026-01-10, while its JSON publication time and visible announcement date are 2020-12-31. The relationship between 584083 and 618392 is reciprocal in the existing captured route data. Keep the URL-date discrepancy as a separate unresolved URL/date anomaly; do not convert it into a reciprocity failure.

## Scope

- No edits to original captures or the v6 forensic counter.
- No inferred reverse edges.
- No production dataset, PIT, training or backtest admission.
- Full SZSE graph acceptance remains false pending exact-route evidence and broader coverage qualification.
