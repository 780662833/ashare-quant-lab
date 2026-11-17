"""Capture a bounded sample from the SZSE 本所公告 archive.

No PDFs or other attachments are downloaded. All captures remain isolated from
CNEquity and production datasets.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ashare_quant.research.szse_notice_archive import probe_szse_notice_archive


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a bounded number of SZSE 本所公告 listing/article HTML pages. "
            "The tool never downloads attachments and never claims archive completeness."
        )
    )
    parser.add_argument("--start-page", type=int, default=0, help="0=index.html, 1=index_1.html, etc.")
    parser.add_argument("--max-pages", type=int, default=3, help="Hard cap: 1-10 listing pages")
    parser.add_argument("--max-articles", type=int, default=20, help="Hard cap: 1-50 notice HTML pages")
    parser.add_argument("--keyword", default="", help="Optional local title filter; no search API is called")
    parser.add_argument("--start-date", help="Optional inclusive publication date YYYY-MM-DD")
    parser.add_argument("--end-date", help="Optional inclusive publication date YYYY-MM-DD")
    parser.add_argument(
        "--article-url",
        action="append",
        default=[],
        help="Optional official SZSE 本所公告 URL seed; can be supplied more than once",
    )
    parser.add_argument("--minimum-interval-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--output-dir",
        default="reports/source_probes/szse_notices",
        help="Raw HTML and JSON report output directory; outside the CNEquity lake",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually issue the bounded read-only HTTP GET requests",
    )
    args = parser.parse_args()

    if not args.execute:
        plan = {
            "mode": "PLAN_ONLY_NO_NETWORK",
            "start_page": args.start_page,
            "max_pages": args.max_pages,
            "max_articles": args.max_articles,
            "keyword": args.keyword,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "article_urls": args.article_url,
            "minimum_interval_seconds": args.minimum_interval_seconds,
            "timeout_seconds": args.timeout_seconds,
            "production_admitted": False,
            "pdf_attachments_downloaded": False,
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print("\nNo network request was made. Add --execute to capture the bounded sample.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / f"szse_notice_capture_{stamp}"
    try:
        report = probe_szse_notice_archive(
            output_dir=output_dir,
            start_page=args.start_page,
            max_pages=args.max_pages,
            max_articles=args.max_articles,
            min_interval_seconds=args.minimum_interval_seconds,
            timeout_seconds=args.timeout_seconds,
            keyword=args.keyword,
            start_date=args.start_date,
            end_date=args.end_date,
            article_urls=args.article_url,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    counters = report["counters"]
    print(f"Capture status: {report['status']}")
    print(f"Archive pages captured: {counters['archive_pages_captured']}")
    print(f"Notice links discovered: {counters['discovered_notice_links']}")
    print(f"Notice pages captured: {counters['notice_pages_captured']}")
    print("Archive completeness proven: False")
    print("PIT admitted: False")
    print(f"Report: {(output_dir / report['report_file']).resolve()}")
    return 0 if report["status"] == "CAPTURED_BOUNDED_SAMPLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
