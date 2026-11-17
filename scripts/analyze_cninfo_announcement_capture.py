"""Classify a previously captured CNINFO announcement page without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ashare_quant.research.cninfo_probe import classify_cninfo_announcement_row


def analyze_capture(source_path: Path) -> dict[str, Any]:
    """Read and classify one raw CNINFO query JSON file entirely offline."""
    raw = source_path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("capture JSON root must be an object")
    announcements = payload.get("announcements")
    if not isinstance(announcements, list):
        raise ValueError("capture JSON must contain an announcements array")

    classified_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row_index, row in enumerate(announcements):
        if not isinstance(row, dict):
            classified_rows.append(
                {
                    "row_index": row_index,
                    "classification": "INVALID_ANNOUNCEMENT_ROW",
                    "reason": "announcement row is not a JSON object",
                    "requires_original_document_review": True,
                }
            )
            counts["INVALID_ANNOUNCEMENT_ROW"] += 1
            continue

        result = classify_cninfo_announcement_row(row)
        entry = {
            "row_index": row_index,
            "sec_code": row.get("secCode"),
            "sec_name": row.get("secName"),
            "announcement_id": row.get("announcementId"),
            "announcement_title_raw": row.get("announcementTitle"),
            "normalized_title": result["normalized_title"],
            "adjunct_url": row.get("adjunctUrl"),
            "page_column": row.get("pageColumn"),
            "announcement_time_raw": row.get("announcementTime"),
            "classification": result["classification"],
            "decision_or_result_candidate": result["decision_or_result_candidate"],
            "requires_original_document_review": result[
                "requires_original_document_review"
            ],
            "classification_basis": result["classification_basis"],
        }
        classified_rows.append(entry)
        counts[result["classification"]] += 1

    return {
        "schema_version": 1,
        "mode": "OFFLINE_CAPTURE_ANALYSIS_NO_NETWORK",
        "source_file": source_path.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "reported_total_records": payload.get("totalRecordNum"),
        "reported_total_pages": payload.get("totalpages"),
        "has_more": payload.get("hasMore"),
        "captured_rows": len(announcements),
        "classification_counts": dict(sorted(counts.items())),
        "rows": classified_rows,
        "safety": {
            "network_access": False,
            "adjunct_documents_downloaded": False,
            "source_coverage_proven": False,
            "pit_admitted": False,
            "training_unlocked": False,
            "backtest_admitted": False,
        },
        "notes": [
            "Title classification is triage only and is not a legal event determination.",
            "Every termination-related candidate requires original-document review.",
            "The script never follows adjunctUrl or makes any network request.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify an existing CNINFO raw JSON capture offline. "
            "No endpoint is queried and no adjunct PDF is downloaded."
        )
    )
    parser.add_argument("capture", type=Path, help="Path to a saved page_NNN.json file")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path; stdout is used when omitted",
    )
    args = parser.parse_args()

    try:
        report = analyze_capture(args.capture)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Analysis report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
