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

## Execution limitation and current result

The exact route URLs and their .json counterparts were attempted from the current execution environment on 2026-11-20. Requests failed before HTTP with DNS resolution errors for www.szse.cn; the web retrieval route also reported those exact URLs inaccessible.

This is an execution-environment limitation, not evidence that the SZSE routes are invalid or missing. No live response, HTTP status or new SHA-256 is claimed here.

## Related anomaly kept separate

The 618392 capture has a URL path date segment 2026-01-10, while its JSON publication time and visible announcement date are 2020-12-31. The relationship between 584083 and 618392 is reciprocal in the existing captured route data. Keep the URL-date discrepancy as a separate unresolved URL/date anomaly; do not convert it into a reciprocity failure.

## Scope

- No edits to original captures or the v6 forensic counter.
- No inferred reverse edges.
- No production dataset, PIT, training or backtest admission.
- Full SZSE graph acceptance remains false pending exact-route evidence and broader coverage qualification.
