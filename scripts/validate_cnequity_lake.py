from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ashare_quant.data.acceptance import DEFAULT_PIT_ITEMS, render_markdown, run_acceptance


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run fail-closed four-gate acceptance against a local CNEquity lake."
    )
    parser.add_argument("--data-root", default=r"F:\\cnEquity")
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument(
        "--pit-items",
        nargs="+",
        default=list(DEFAULT_PIT_ITEMS),
        help="Documented CNEquity financial item codes (default: roe net_profit)",
    )
    parser.add_argument(
        "--profile",
        default="cn_a_sh_sz_research_v1",
    )
    parser.add_argument("--output-dir", default="reports/data_acceptance")
    args = parser.parse_args()

    report = run_acceptance(
        data_root=args.data_root,
        start_date=args.start_date,
        end_date=args.end_date,
        pit_items=args.pit_items,
        profile=args.profile,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"cnequity_acceptance_{stamp}.json"
    md_path = output_dir / f"cnequity_acceptance_{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Status: {report['overall_status']}")
    for gate in report["gates"]:
        print(f"{gate['status']:4} {gate['name']}")
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {md_path.resolve()}")
    return 0 if report["overall_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
