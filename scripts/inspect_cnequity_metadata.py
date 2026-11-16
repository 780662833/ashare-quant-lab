"""Run a read-only inspection of the local CNEquity instrument master."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ashare_quant.data.metadata_inspection import inspect_cnequity_metadata

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect local CNEquity instruments and delisting metadata without backfill."
    )
    parser.add_argument(
        "--data-root",
        default=r"F:\cnequity",
        help=r"CNEquity lake directory (default: F:\cnequity)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "reports" / "data_diagnostics"),
        help="Directory for the JSON inspection report",
    )
    args = parser.parse_args()

    report = inspect_cnequity_metadata(args.data_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"cnequity_metadata_inspection_{stamp}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print(f"CNEquity instruments: {report['overall_read_status']}")
    for dataset, result in report["datasets"].items():
        print(
            f"{dataset}: {result.get('status', 'ERROR')}"
            + (f" rows={result['row_count']}" if "row_count" in result else "")
        )

    instruments = report["datasets"].get("instruments", {})
    missingness = instruments.get("listing_date_missingness", {})
    if missingness.get("status") == "AVAILABLE":
        print(
            "list_date missing/unparseable: "
            f"{missingness['missing_or_unparseable_rows']}/{missingness['total_rows']}"
        )
        print(
            "A-share stock list_date missing: "
            f"{missingness['a_share_stock_missing_list_date_rows']}/"
            f"{missingness['a_share_stock_rows']}"
        )
        print(
            "Other/unqualified stock list_date missing: "
            f"{missingness['unqualified_stock_missing_list_date_rows']}"
        )
        print(
            "Non-stock list_date missing: "
            f"{missingness['non_stock_missing_list_date_rows']}"
        )
        print(
            "Full missing A-share stock symbol list: "
            "see listing_date_missingness.a_share_stock_missing_records in JSON"
        )

    listing_events = report.get("cross_checks", {}).get("listing_event_evidence", {})
    print(
        "Official listing-event evidence: "
        f"{listing_events.get('status', 'NOT_EVALUABLE')}"
    )
    print(
        "Listing-date PIT admission: "
        f"{instruments.get('listing_date_admission', {}).get('status', 'NOT_ADMITTED')}"
    )
    print(f"JSON: {output_path.resolve()}")
    return 0 if report["overall_read_status"] == "READ" else 2


if __name__ == "__main__":
    raise SystemExit(main())
