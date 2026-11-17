# SZSE 本所公告 bounded archive capture (Phase 9.35)

## Purpose and status

This phase adds a **bounded, read-only HTML capture utility** for the Shenzhen Stock Exchange (SZSE) 本所公告 page. It is an evidence-preservation tool and an archive-discovery experiment, not a production event adapter.

Publicly indexed SZSE pages show the 本所公告 listing at:

- https://www.szse.cn/disclosure/notice/index.html
- https://www.szse.cn/www/disclosure/notice/index_1.html
- https://www.szse.cn/www/disclosure/notice/index_5.html

The 002604 *ST龙力 exchange notice uses the detail-page path:

- https://www.szse.cn/disclosure/notice/t20200522_577530.html

The `index_N.html` sequence and `tYYYYMMDD_ID.html` detail path are observed public-page conventions. They are not a documented SZSE API contract. The public material reviewed does not disclose a reliable total-page count or prove that sequential list pages expose the complete historical archive. In particular, a small number of recent index pages must not be interpreted as coverage back to 2020.

Review date: 2026-11-16.

## What the utility does

The implementation is in:

- `src/ashare_quant/research/szse_notice_archive.py`
- `scripts/capture_szse_notice_archive.py`
- `tests/unit/test_szse_notice_archive.py`

The script:

1. Fetches only HTTPS HTML pages on the exact host `www.szse.cn`.
2. Captures a caller-selected contiguous list-page range using the observed `index.html`, `index_1.html`, ... naming pattern.
3. Extracts candidate article links that match the 本所公告 detail-page pattern `tYYYYMMDD_ID.html`, including links encoded in per-row `curHref` / `curTitle` script variables.
4. Applies optional keyword and publication-date filters locally to the captured list-page candidates; it does not call an undocumented search endpoint.
5. Captures at most the selected number of article HTML pages. An operator can also supply an exact official article URL as a seed; the report labels it `operator_supplied_seed` rather than pretending the archive discovered it.
6. Saves the original HTML bytes and SHA-256 digest for every captured page, along with URL, response status, request/capture timestamps, extracted record ID, title and date observations.
7. Does not follow PDF or other attachment links.

Hard limits:

- 1–10 listing pages per run;
- 1–50 article pages per run;
- request spacing at least 0.5 seconds;
- response body limit 20 MiB per HTML page;
- official SZSE host allowlist; redirects outside the allowlist fail closed.

The report always sets `archive_completeness_proven=false`, `pit_admitted=false`, `training_unlocked=false` and `backtest_admitted=false`.

## Usage

Print a plan without making a network request:

```powershell
python scripts/capture_szse_notice_archive.py --start-page 0 --max-pages 3 --max-articles 20
```

Capture a bounded sample from the first three list pages:

```powershell
python scripts/capture_szse_notice_archive.py --start-page 0 --max-pages 3 --max-articles 20 --execute
```

Capture the known 002604 exchange notice as an explicitly supplied control seed while also capturing one listing page:

```powershell
python scripts/capture_szse_notice_archive.py --start-page 0 --max-pages 1 --max-articles 1 --article-url "https://www.szse.cn/disclosure/notice/t20200522_577530.html" --execute
```

The output is isolated under `reports/source_probes/szse_notices/`:

- `archive_page_XXXXX.html`: original listing-page bytes;
- `notice_YYYYMMDD_ID.html`: original notice-page bytes;
- `capture_report.json`: request, response, discovery, capture and safety manifest.

The `--article-url` argument is an operator-provided control record. It is not proof that the list archive can discover that record. Explicit seeds are prioritized within the article cap and are labeled in the report.

## Offline inspection of captured HTML

The capture command preserves the original listing HTML rather than guessing how visible search controls submit requests. Inspect a captured page without making another network request:

```powershell
python scripts/inspect_szse_notice_archive_html.py --html "reports/source_probes/szse_notices/<capture-run>/archive_page_00000.html" --output "reports/source_probes/szse_notices/<capture-run>/archive_page_00000.inspection.json"
```

The inspector reports form actions/methods, visible control attributes (including class, selected data-* fields and inline click/change handlers), first-party script references, inline-script SHA-256 fingerprints, candidate request routes, likely payload keys near request calls, and bounded code clues around DOM lookups, jQuery selectors and common event handlers (`addEventListener`, `.on`, `.click`, `.bind`, `.delegate`, and related forms). The report also emits short, bounded inline-script contexts around the known query-control selectors (`confirm-query`, `btn-query-primary`, `article-text`, `aticle-datepicker`) and page-initialization identifiers (`article-template`, `createPageHTML`, `artInitHtml`, `cmsParam`), with per-script offsets for manual source review. It does not execute JavaScript or fetch external scripts. Hidden input values are omitted; snippet redaction covers common standalone and compound credential names such as `access_token`, `refresh_token`, `csrfToken`, `apiKey` and `session_id`. When inspecting a detail page or a non-default listing page, pass its exact captured source URL with `--page-url` so relative references resolve correctly.

Candidate routes, request calls and payload keys are investigative clues, not a verified API contract. The inspector output must be reviewed against the original HTML and script context before implementing any live request. Until then, `--keyword`, `--start-date` and `--end-date` remain local filters over already captured list-page candidates; they are not server-side search options.

## Parser and evidence limitations

The parser supports ordinary article anchors and the observed inline `curHref` / `curTitle` form. This is intentionally best-effort HTML parsing, not a frozen SZSE schema. A page with an unrecognized DOM may produce zero candidates; zero extracted links means only that the parser did not recognize a link on that captured page.

`publication_date_observed` is extracted from date text on the list item when available and otherwise falls back to the date embedded in the detail-page path. The detail page's visible text is retained for human review. The script does not claim that this value is an exact intraday first-publication timestamp. Capture time is kept separately and is never substituted for publication time.

The report preserves the raw source HTML so a later parser revision can reprocess the original bytes without another source request. It does not yet create the full listing-event evidence envelope: there is no independently audited source revision, contract fingerprint, replayable read receipt or completeness reconciliation in this phase.

## 002604 control record

The control URL is the official SZSE exchange-issued notice:

https://www.szse.cn/disclosure/notice/t20200522_577530.html

It identifies the 2020-05-22 decision to terminate listing of 山东龙力生物科技股份有限公司 (*ST龙力, 002604) and states that the security would enter its 30-trading-day delisting consolidation period on 2020-06-01.

This URL is suitable as an operator-supplied parser/capture control. A successful capture verifies only that the script can retain that one official page. It does not test that the archive list pages discover the 2020 record.

## Next qualification gates

Before implementing a production SZSE delisting-event dataset:

1. Inspect the captured list HTML and determine whether the list pages expose a supported date-range/search route. Do not guess POST endpoints or hidden parameters.
2. Establish the archive's actual pagination boundary and historical date coverage with a reproducible source-supported method. The 10-page script cap is an operational safety bound, not an archive-completeness claim.
3. Test the parser against multiple ordinary notices, listing decisions, termination decisions, delisting-period notices and final delisting notices across older and newer periods.
4. Preserve original HTML/PDF bytes and stable record locators; attachment downloads require a separate explicit allowlisted step and must be reconciled to the detail page before being treated as source evidence.
5. Reconcile the full archive to the historical SZSE universe, including delisted, renamed and code-migrated issuers.
6. Determine public-release timestamp semantics. A date-only page must receive a conservative 23:59:59 Asia/Shanghai availability cutoff; request/capture time is not publication time.
7. Only after the above, create replayable source receipts and run missing-event, duplicate, conflict, temporal and full-universe audits.

## Safety statement

This tool makes only bounded, rate-limited, read-only GET requests when `--execute` is supplied. It captures HTML and metadata in the selected report directory. It does not download PDFs or other attachments, access the local CNEquity lake, change the CNEquity lock, write production datasets, backfill, train a model or run a backtest. A successful bounded capture is not a production qualification.
