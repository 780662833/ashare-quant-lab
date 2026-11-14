from __future__ import annotations

"""Print the CNEquity instruments backfill plan without fetching or writing data.

The command invokes CNEquity's own plan-only path. It reads the local config
and source policy but does not make network requests, create a run, or mutate
the data lake.
"""

import argparse
import sys

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
