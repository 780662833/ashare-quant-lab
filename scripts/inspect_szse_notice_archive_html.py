"""Inspect a previously captured SZSE 本所公告 HTML file without network access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ashare_quant.research.szse_notice_html_inspector import (
    inspect_szse_notice_html,
    write_szse_html_inspection_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline inspection of captured SZSE HTML forms and script references; "
            "no network requests are made."
        )
    )
    parser.add_argument("--html", required=True, help="Path to one captured .html file")
    parser.add_argument(
        "--page-url",
        default="https://www.szse.cn/disclosure/notice/index.html",
        help="Canonical source page URL used only to resolve relative references",
    )
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    try:
        report = inspect_szse_notice_html(args.html, page_url=args.page_url)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2

    if args.output:
        output = write_szse_html_inspection_report(report, args.output)
        print(f"Inspection report: {output.resolve()}")
    discovery = report["query_discovery"]
    candidate_routes = ", ".join(discovery["candidate_routes"]) or "none detected"
    print(f"Candidate request routes: {candidate_routes}")
    print(f"Request-call clues: {discovery['request_call_clue_count']}")
    print(f"Query-binding clues: {discovery['binding_clue_count']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nOffline inspection only. No network request was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
