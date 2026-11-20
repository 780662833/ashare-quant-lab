# SZSE URL-path date versus publication-date investigation (2026-11-20)

## Result

The tYYYYMMDD suffix in a SZSE announcement URL must not be treated as the publication date. In the supplied 66-node capture, 64 URL path dates equal the JSON/HTML publication date and 2 do not. For both mismatches, the HTML visible date agrees with the JSON pubTime when interpreted in Asia/Shanghai.

This is a metadata-semantics finding, not evidence that the publication timestamps are wrong.

## Source capture and method

Input archive: szse_year_boundary_audit.zip

- SHA-256: 19067beecb0d8050eeaa2e1205a6dc8fffd5d686d6459d8280ecf85946600d5e
- 342 ZIP entries.
- 66 node records.
- For each node, compare the date in its detail URL path (tYYYYMMDD_docId.html) with:
  - nodes.json publish_time;
  - pages/<docId>/announcement.json data.pubTime, converted to Asia/Shanghai;
  - the visible date in the captured HTML time field.

The JSON timestamp and HTML visible date agree for all 66 nodes. The date-delta distribution (URL path date minus publication date) is:

| Path-date delta | Node count |
|---:|---:|
| 0 days | 64 |
| +1 day | 1 |
| +1836 days | 1 |

No node in this bounded sample has an HTML/JSON publication-date conflict.

## The two path-date mismatches

| docId | URL path date | JSON / HTML publication date | Delta | Title |
|---|---|---|---:|---|
| 584158 | 2021-01-08 | 2021-01-07 | +1 day | 关于发布《深圳证券交易所创业板行业信息披露指引第14号——上市公司从事通信相关业务》《深圳证券交易所创业板行业信息披露指引第15号——上市公司从事网络安全相关业务》的通知 |
| 618392 | 2026-01-10 | 2020-12-31 | +1836 days | 关于不再聘任郁亮为创业板第一届上市委员会委员的公告 |

### 584158 — one-day path-date difference

Captured source evidence:

- URL: https://www.szse.cn/disclosure/notice/general/t20210108_584158.html
- nodes.json publish_time: 2021-01-07 00:00:00
- HTML visible date: 2021-01-07
- JSON data.pubTime: 1609948800000, which is 2021-01-07 00:00:00 Asia/Shanghai.
- The announcement body is signed by SZSE on 2021-01-07.

An independently accessible reproduction of this SZSE notice also gives the issue date as 2021-01-07 while linking to a SZSE detail URL whose path contains t20210108_584158.html. This is a concrete counterexample to interpreting the path suffix as the authoritative publication date.

### 618392 — large path-date difference

Captured source evidence:

- URL: https://www.szse.cn/disclosure/notice/general/t20260110_618392.html
- nodes.json publish_time: 2020-12-31 00:00:00
- HTML visible date: 2020-12-31
- JSON data.pubTime: 1609344000000, which is 2020-12-31 00:00:00 Asia/Shanghai.
- The announcement body itself is signed 2020年12月31日 and contains document number 深证上〔2020〕1280号.
- HTML articleId and JSON docId both equal 618392; the captured page and JSON identity check passed.
- The node is in navigation channel 212 / general_news; its captured neighbor response has chnlDocId=212_618392.
- The neighbor response places it between 584083 (publication date 2021-01-04) and 584051 (publication date 2020-12-31). The corresponding source relations are reciprocal in the original archive.

External corroboration:

- SZSE's current 业务通知 listing displays this exact title with date 2020-12-31. This supports the captured HTML/JSON publication date.
- 证券时报/每日经济新闻 separately reported the same announcement on 2020-12-31 at 23:11:27.

The publication date 2020-12-31 is therefore supported by the captured HTML, JSON timestamp, signed body, neighbor chronology, SZSE's current listing and independent contemporaneous reporting. The URL path date remains 2026-01-10.

## Classification

For 618392:

- publication_date_status: corroborated_2020-12-31.
- url_path_date_status: mismatch_2026-01-10.
- root_cause: unresolved.
- source_publication_date_error_confirmed: false.

A later CMS ingestion, migration, republishing or route-maintenance date is a plausible explanation for the 2026 path suffix, but the supplied evidence does not identify which mechanism occurred. Do not silently rewrite the official URL, substitute 2026-01-10 as the publication date, or label the source publication date erroneous.

For 584158, retain the same separation: the URL path date is one day later than the HTML/JSON/body publication date. Its presence reinforces that the route suffix is not a safe publication-date field.

## Required data-model and audit treatment

Keep these observations as separate fields:

1. url_path_date — parsed only from the URL locator.
2. json_pub_time — source JSON publication timestamp.
3. html_visible_date — date displayed by the announcement page.
4. body_signed_date — date printed in the announcement body, when extractable.
5. capture_time — when the response was collected.

Publication-date logic must not fall back to the URL path date merely because the fields often coincide. A path-date mismatch is a diagnostic to preserve and investigate; it is not by itself proof of an incorrect publication timestamp.

## Remaining route-variant check for 618392

The captured exact route is valid and identity-consistent, but the root cause of the path segment is still unknown. A bounded GET-only comparison of these candidates can test whether a publication-date-shaped alternate URL exists:

- Existing route: https://www.szse.cn/disclosure/notice/general/t20260110_618392.html
- Date-aligned general route candidate: https://www.szse.cn/disclosure/notice/general/t20201231_618392.html
- Date-aligned plain notice route candidate: https://www.szse.cn/disclosure/notice/t20201231_618392.html

A 404 or failed candidate must remain a failed route observation, not be converted into an alias. A candidate can be classified as the same business document only after HTML/JSON identity and payload checks pass. This comparison is not required to retain 2020-12-31 as the publication date.

## Acceptance boundary

- The 77-edge bounded reciprocity result remains unchanged.
- This investigation does not prove the complete SZSE historical archive.
- It does not alter v6 forensic counters or create inferred graph edges.
- Full graph, production, PIT, training and backtest admission remain false.
