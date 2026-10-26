"""Headless command line entry points. No PySide6 import on this path.

    python -m token_tracker headroom            # short table
    python -m token_tracker headroom --json     # schema documented in README
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMANDS = ("headroom",)


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m token_tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser(
        "headroom",
        help="remaining usage-limit headroom per provider, from local credential-free sources",
        description="Remaining usage-limit headroom per provider. Reads Codex rollout rate_limits, "
                    "the opt-in Claude status line ledger, and OpenRouter /api/v1/key only when "
                    "OPENROUTER_API_KEY is already set. Missing or old data is marked, never filled in.",
    )
    h.add_argument("--json", action="store_true", help="print the versioned JSON document")
    h.add_argument("--out", type=Path, help="also write the JSON document to this file (atomic)")
    h.add_argument("--offline", action="store_true", help="skip network sources (OpenRouter)")
    h.add_argument("--codex-dir", type=Path, help="Codex sessions dir (default: auto-detect)")
    h.add_argument("--claude-file", type=Path, help="Claude status line ledger (default: see README)")
    h.add_argument("--stale-after", type=_positive_float, metavar="SECONDS",
                   help="mark readings older than this stale (default: a quarter of the window, max 6h)")
    h.add_argument("--max-codex-files", type=_positive_int, default=None, metavar="N",
                   help="newest rollout files to scan (default: 40)")
    return parser


def cmd_headroom(args: argparse.Namespace, stdout) -> int:
    from . import headroom

    kwargs = {}
    if args.max_codex_files:
        kwargs["max_codex_files"] = args.max_codex_files
    report = headroom.build_report(
        codex_root=args.codex_dir,
        claude_file=args.claude_file,
        offline=args.offline,
        stale_after=args.stale_after,
        **kwargs,
    )
    if args.out:
        headroom.write_json_atomic(report, args.out)
    if args.json:
        stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        stdout.write(headroom.render_table(report))
    return 0


def main(argv: list[str] | None = None, stdout=None) -> int:
    args = build_parser().parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    if args.command == "headroom":
        return cmd_headroom(args, out)
    return 2  # unreachable: argparse rejects unknown commands


if __name__ == "__main__":
    sys.exit(main())
