"""
CLI entrypoint.

    python make_videos.py --count 1                     # Gemini backend
    python make_videos.py --backend local --count 1     # keyless backend
    python make_videos.py --backend local --dry-run     # check, make nothing

``--backend`` chooses where the text comes from; ``TEXT_BACKEND`` in the
environment is the fallback when the flag is omitted, and Gemini is the
default. Only the Gemini backend needs ``GEMINI_API_KEY``.

Exit codes: 0 when at least one video was produced, 1 on setup failure or a
total failure, 2 on a partial failure (some videos produced, some not).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.config import BACKENDS, DEFAULT_BACKEND, ConfigError, normalize_backend
from app.pipeline import run_pipeline

_BACKEND_HELP = {
    "gemini": "ideas, script, caption and voice from the Gemini API "
              "(needs GEMINI_API_KEY)",
    "local": "text from local_text.json, voice from Edge TTS (no key, no API)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_videos.py",
        description="Generate vertical TikTok videos from fictional AI-written stories.",
        epilog=(
            "Backends: "
            + "; ".join(f"{name} - {_BACKEND_HELP[name]}" for name in BACKENDS)
            + f". Defaults to $TEXT_BACKEND, else {DEFAULT_BACKEND}."
        ),
    )
    parser.add_argument(
        "--count", type=int, default=3, help="How many videos to generate (default: 3)"
    )
    parser.add_argument(
        "--backend",
        default=None,
        metavar="NAME",
        help=(
            f"Text backend: {' or '.join(BACKENDS)} "
            f"(default: $TEXT_BACKEND, else {DEFAULT_BACKEND})"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check configuration and dependencies, then exit.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 1

    # Reject an unknown backend here, so argparse's own error path is not the
    # only thing between a typo and a confusing mid-run failure.
    if args.backend is not None:
        try:
            normalize_backend(args.backend)
        except ConfigError as e:
            parser.error(str(e))  # exits with code 2

    result = run_pipeline(args.count, args.dry_run, backend=args.backend)
    success = result.get("success") or []
    failed = result.get("failed") or []

    if args.dry_run:
        if failed:
            print(f"DRY-RUN FAILED (backend '{result.get('backend') or 'unknown'}')")
            for item in failed:
                print(f"  - {item.get('error')}", file=sys.stderr)
            return 1
        print(f"DRY-RUN OK: backend '{result.get('backend')}' is ready.")
        return 0

    for item in success:
        print(f"OK   {item['file']}  ({item['duration']}s)")
        styles = item.get("visual_styles")
        if isinstance(styles, list) and styles:
            print(f"     {item.get('scenes')} scenes: {', '.join(sorted(set(styles)))}")

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
