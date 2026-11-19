"""cost-guard command line.

Exit codes:
  0  scan complete, total under --fail-over (or no budget gate set)
  1  estimated waste is over --fail-over USD/month
  2  bad usage, or one or more checks failed (report is partial)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, NoCredentialsError

from . import __version__
from .checks import CHECKS
from .config import load_settings
from .report import post_slack, slack_payload, to_json, to_markdown, to_table
from .scanner import scan

RENDERERS = {"table": to_table, "markdown": to_markdown, "json": to_json}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cost-guard",
        description="Find AWS waste (read-only) and estimate what it costs per month.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-c", "--config", type=Path, help="TOML settings file")
    p.add_argument(
        "-r",
        "--region",
        action="append",
        dest="regions",
        help="region to scan (repeatable); overrides the config file",
    )
    p.add_argument(
        "--check",
        action="append",
        dest="checks",
        choices=sorted(CHECKS),
        help="run only this check (repeatable)",
    )
    p.add_argument("-f", "--format", choices=sorted(RENDERERS), default="table")
    p.add_argument("-o", "--output", type=Path, help="write the report here instead of stdout")
    p.add_argument(
        "--fail-over",
        type=float,
        metavar="USD",
        help="exit 1 if estimated monthly waste exceeds this amount",
    )
    p.add_argument(
        "--slack", action="store_true", help="post a summary to the webhook in $SLACK_WEBHOOK_URL"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        print(f"cost-guard: {exc}", file=sys.stderr)
        return 2
    if args.regions:
        settings.regions = args.regions
    if args.checks:
        settings.checks = args.checks

    try:
        result = scan(settings)
    except NoCredentialsError:
        print(
            "cost-guard: no AWS credentials found (use a profile, SSO or OIDC role)",
            file=sys.stderr,
        )
        return 2
    except (BotoCoreError, ValueError) as exc:
        print(f"cost-guard: {exc}", file=sys.stderr)
        return 2

    report = RENDERERS[args.format](result)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    else:
        print(report)

    if args.slack:
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("cost-guard: --slack given but SLACK_WEBHOOK_URL is not set", file=sys.stderr)
            return 2
        try:
            post_slack(webhook, slack_payload(result))
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"cost-guard: Slack post failed: {exc}", file=sys.stderr)
            return 2

    if result.errors:
        return 2
    if args.fail_over is not None and result.total_monthly > args.fail_over:
        print(
            f"cost-guard: ${result.total_monthly:,.2f}/month is over the "
            f"${args.fail_over:,.2f} budget",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
