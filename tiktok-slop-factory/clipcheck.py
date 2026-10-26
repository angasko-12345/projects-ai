"""
clipcheck: lint a finished vertical clip (and its post text) against a campaign brief.

    python clipcheck.py VIDEO --campaign campaign.toml
    python clipcheck.py VIDEO --campaign campaign.toml --post post.txt --json

Read-only: the video is analysed with FFmpeg/ffprobe and never modified,
re-encoded or uploaded. Works on any MP4, including CapCut exports.

This is a separate entry point rather than a ``make_videos.py check``
subcommand because ``make_videos.py`` is a flag-only generator whose exit code
2 means "partial batch"; here 2 means "warnings". Two meanings for one code
on one command would make scripts that call either one ambiguous.

Exit codes: 0 every check passed (or was not configured), 2 at least one
warning, 1 at least one failure, or the check could not run at all (bad
arguments, unreadable campaign file, missing post file, FFmpeg missing).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.campaign import CampaignError, load_campaign
from app.qc import QCError, format_table, run_checks


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; 2 means 'warnings' here, so use 1."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="clipcheck.py",
        description="Check a finished vertical clip and its post text against a "
                    "campaign brief (read-only).",
        epilog="Exit codes: 0 pass, 2 warnings, 1 failures or the check could not run.",
    )
    parser.add_argument("video", help="The finished clip (MP4 or anything FFmpeg reads)")
    parser.add_argument("--campaign", required=True, metavar="FILE",
                        help="Campaign brief TOML (see examples/campaign.example.toml)")
    parser.add_argument("--post", metavar="FILE",
                        help="Plain-text file holding exactly the post description")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    parser.add_argument("--threads", type=int, default=1, metavar="N",
                        help="FFmpeg threads per analysis pass (default 1; 0 = FFmpeg decides)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.threads < 0:
        parser.error("--threads must be 0 or more")

    try:
        campaign = load_campaign(args.campaign)
    except CampaignError as e:
        print(f"clipcheck: {e}", file=sys.stderr)
        return 1

    post_text = None
    if args.post:
        try:
            post_text = Path(args.post).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            print(f"clipcheck: cannot read post file {args.post}: {e}", file=sys.stderr)
            return 1

    try:
        report = run_checks(Path(args.video), campaign, post_text,
                            post_path=args.post, threads=args.threads or None)
    except QCError as e:
        print(f"clipcheck: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(report.to_json())
    else:
        print(format_table(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
