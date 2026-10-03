"""
CLI entrypoint.

Exit codes: 0 when at least one video was produced, 1 on setup failure or a
total failure, 2 on a partial failure (some videos produced, some not).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_videos.py",
        description="Generate vertical TikTok videos from fictional AI-written stories.",
    )
    parser.add_argument(
        "--count", type=int, default=3, help="How many videos to generate (default: 3)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check configuration and dependencies, then exit.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 1

    result = run_pipeline(args.count, args.dry_run)
    success = result.get("success") or []
    failed = result.get("failed") or []

    if args.dry_run:
        if failed:
            print("DRY-RUN FAILED")
            for item in failed:
                print(f"  - {item.get('error')}", file=sys.stderr)
            return 1
        print("DRY-RUN OK: config and dependencies are ready.")
        return 0

    for item in success:
        print(f"OK   {item['file']}  ({item['duration']}s)")
        credit = item.get("footage_by")
        if isinstance(credit, str) and credit:
            print(f"     footage by {credit} on Pexels")

    for item in failed:
        print(f"FAIL {item.get('idea') or item.get('step')}: {item.get('error')}",
              file=sys.stderr)

    if not success:
        print("No videos were produced.", file=sys.stderr)
        return 1
    if failed:
        print(f"Partial run: {len(success)} succeeded, {len(failed)} failed.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
