"""Capture a bounded set of exact SZSE announcement routes for graph forensics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ashare_quant.research.szse_route_context import (
    DEFAULT_ROUTE_URLS,
    capture_szse_route_context,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only GET capture of exact SZSE detail routes and route-scoped neighbors."
    )
    parser.add_argument("--route-url", action="append", default=[])
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--minimum-interval-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("reports/source_probes/szse_route_context"),
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    route_urls = tuple(args.route_url) if args.route_url else DEFAULT_ROUTE_URLS
    if not args.execute:
        print(json.dumps({
            "mode": "PLAN_ONLY_NO_NETWORK", "route_urls": route_urls,
            "reference_dir": str(args.reference_dir) if args.reference_dir else None,
            "maximum_requests": len(route_urls) * 3, "methods": ["GET"],
            "minimum_interval_seconds": args.minimum_interval_seconds,
            "output_dir": str(args.output_dir), "attachments_downloaded": False,
            "full_graph_acceptance": False,
        }, ensure_ascii=False, indent=2))
        print("No network request was made. Add --execute to capture these exact routes.")
        return 0
    try:
        report = capture_szse_route_context(
            output_dir=args.output_dir, route_urls=route_urls,
            reference_dir=args.reference_dir,
            min_interval_seconds=args.minimum_interval_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {report['report_file']}")
    summary = report["summary"]
    return 0 if (
        report["status"] == "CAPTURED_BOUNDED_ROUTE_SAMPLE"
        and summary["routes_captured_identity_match"] == len(route_urls)
        and summary["reverse_checks_mismatch"] == 0
        and summary["reverse_checks_unresolved"] == 0
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
