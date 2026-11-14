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
        default=str(ROOT / "data" / "cnequity"),
        help="CNEquity lake directory (default: project data/cnequity)",
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
    print("Listing-date PIT admission: NOT_ADMITTED")
    print(f"JSON: {output_path.resolve()}")
    return 0 if report["overall_read_status"] == "READ" else 2


if __name__ == "__main__":
    raise SystemExit(main())
