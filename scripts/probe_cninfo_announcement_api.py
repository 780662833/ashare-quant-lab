"""Run an explicitly enabled, bounded CNINFO announcement metadata probe."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ashare_quant.research.cninfo_probe import (
    probe_cninfo_announcement_api,
    render_probe_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or execute a bounded, read-only probe of the reverse-engineered "
            "CNINFO announcement search endpoint. No query is sent without --execute."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument(
        "--column",
        choices=("sse", "szse"),
        required=True,
        help="Reverse-engineered exchange selector: sse or szse",
    )
    parser.add_argument(
        "--plate",
        default="",
        help="Reverse-engineered board selector (for example sh, shkcp, sz or szcy)",
    )
    parser.add_argument(
        "--keyword",
        required=True,
        help="One search term, such as 上市公告书 or 终止上市",
    )
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=2,
        help="Hard page cap (1-10); exceeding the server-reported pages is PARTIAL",
    )
    parser.add_argument(
        "--minimum-interval-seconds",
        type=float,
        default=1.0,
        help="Minimum spacing between requests (must be at least 0.5 seconds)",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--output-dir",
        default="reports/source_probes/cninfo",
        help="Probe captures are isolated here, never written into the CNEquity lake",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually make the bounded HTTP requests; without this flag only a plan is printed",
    )
    args = parser.parse_args()

    try:
        plan = render_probe_plan(
            start_date=args.start_date,
            end_date=args.end_date,
            column=args.column,
            plate=args.plate,
            search_key=args.keyword,
            page_size=args.page_size,
            max_pages=args.max_pages,
            min_interval_seconds=args.minimum_interval_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    if not args.execute:
        print(plan)
        print("\nNo network request was made. Add --execute to run this bounded probe.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / f"cninfo_probe_{stamp}"
    report = probe_cninfo_announcement_api(
        start_date=args.start_date,
        end_date=args.end_date,
        column=args.column,
        plate=args.plate,
        search_key=args.keyword,
        output_dir=output_dir,
        page_size=args.page_size,
        max_pages=args.max_pages,
        min_interval_seconds=args.minimum_interval_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    counters = report["query_counters"]
    print(f"Probe status: {report['status']}")
    print(
        "Pages captured: "
        f"{counters['captured_pages']}/{counters['reported_total_pages']}"
    )
    print(
        "Records captured: "
        f"{counters['captured_records']} / {counters['reported_total_records']}"
    )
    print(
        "Page traversal complete for query: "
        f"{report['page_traversal_complete_for_query']}"
    )
    print("PIT admitted: False")
    print(f"Report: {(output_dir / report['report_file']).resolve()}")
    return 0 if report["status"] in {
        "COMPLETE_FOR_QUERY", "COMPLETE_WITH_WARNINGS", "NO_RESULTS"
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
