# SZSE exact-route context capture

## Purpose

A document ID identifies an announcement record but does not uniquely identify its navigation context. The same ID may be reached through a plain notice route, a general route, or a company route. Reciprocity must therefore be checked against the exact URL returned by the neighboring record's source API, not another URL that happens to share the same document ID.

This bounded probe captures the exact detail-page HTML, its same-path JSON, and the previous/next API response using the navigation channel ID embedded in that exact HTML page. The JSON data channel is preserved separately and is never substituted for the HTML navigation channel.

## Known route candidates

- https://www.szse.cn/disclosure/notice/t20210104_584083.html
- https://www.szse.cn/disclosure/notice/company/t20210105_584085.html

Both were returned as neighbor URLs by the original 198-request capture but were absent from that request manifest.

## Usage

From the repository root, print a plan without network access:

    python scripts/capture_szse_route_context.py --reference-dir reports/szse_year_boundary_audit

Execute the two exact route captures:

    python scripts/capture_szse_route_context.py --reference-dir reports/szse_year_boundary_audit --output-dir reports/source_probes/szse_route_context --minimum-interval-seconds 1.0 --execute

The caller may supply one to ten exact URLs using repeated --route-url arguments. Each route uses up to three GET requests: HTML, same-path JSON, and the previous/next API. The neighbor request is skipped unless HTML articleId and JSON data.docId both match the route ID and an HTML navigation channel ID is available.

## Evidence and decision rules

For every response the tool records request/final URL, status, content type, byte count, capture time and SHA-256, and preserves the original response bytes. It extracts HTML articleId/channelId/channelCode; JSON data.docId/title/pubTime/channelId/chnlCode and content hash; and neighbor chnlDocId/preDocId/preDocPuburl/nextDocId/nextDocPuburl.

When --reference-dir is supplied, the captured route JSON is compared with pages/<docId>/announcement.json on document ID, title, publication time and exact content hash.

The known reverse checks are:

- 584082 --pre--> 584083 requires the exact 584083 route's nextDocId to equal 584082.
- 584084 --next--> 584083 requires the exact 584083 route's preDocId to equal 584084.
- 584068 --pre--> 584085 requires the exact 584085 route's nextDocId to equal 584068.

A channel context mismatch or request failure remains unresolved. A reverse mismatch is route-specific evidence, not an automatic SZSE source-data error. The report always sets full_graph_acceptance=false.

## Limits

- Default invocation makes no request; --execute is required.
- HTTPS and exact www.szse.cn host are enforced for requests and redirects.
- Redirects are followed only when the exact request path and query are unchanged; route or channelId/docId-changing redirects are blocked and their Location is recorded.
- Only the observed detail-page path forms and the fixed previous/next API path are accepted.
- Requests are sequential, rate-limited to at least 0.5 seconds, bounded to ten routes and 20 MiB per response.
- Only GET is used. PDFs and other attachments are never downloaded.
- The tool does not modify existing captures, production datasets, CNEquity, PIT status, training eligibility or backtest eligibility.
