from __future__ import annotations

"""Print the CNEquity instruments backfill plan without fetching or writing data.

The command invokes CNEquity's own plan-only path. It reads the local config
and source policy but does not make network requests, create a run, or mutate
the data lake.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from click.testing import CliRunner
from cnequity.cli.main import cli


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan the instruments backfill without executing it."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional CNEquity config path; otherwise use CNEquity's normal resolver.",
    )
    parser.add_argument(
        "--expected-data-root",
        default=str(ROOT / "data" / "cnequity"),
        help="Expected lake root; plan exits non-zero when CNEquity config points elsewhere.",
    )
    args = parser.parse_args()

    command = ["backfill", "instruments", "--plan"]
    if args.config:
        command.extend(["--config", args.config])

    result = CliRunner().invoke(cli, command, catch_exceptions=True)
    if result.output:
        sys.stdout.write(result.output)
    if result.exception is not None and not result.output:
        print(f"{type(result.exception).__name__}: {result.exception}", file=sys.stderr)
    if result.exit_code != 0:
        return result.exit_code or 1

    try:
        plan = json.loads(result.output)
        actual_root = Path(plan["data_root"]).expanduser().resolve()
    except (json.JSONDecodeError, KeyError, TypeError):
        print("Could not verify the planned data_root from CNEquity output.", file=sys.stderr)
        return 2
    expected_root = Path(args.expected_data_root).expanduser().resolve()
    if actual_root != expected_root:
        print(
            f"DATA ROOT MISMATCH: CNEquity will plan against {actual_root}, "
            f"but expected {expected_root}. No data was fetched or written.",
            file=sys.stderr,
        )
        return 2
    print(f"Verified data_root: {actual_root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
