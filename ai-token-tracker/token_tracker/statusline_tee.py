"""Opt-in Claude Code status line tee: record ``rate_limits`` to a local file.

Claude Code pipes a JSON document to the configured status line command on
every update. For Pro/Max subscribers (or behind a gateway with a spend limit)
that JSON carries ``rate_limits.five_hour``, ``rate_limits.seven_day`` and
``rate_limits.spend_limit``: the account's real remaining headroom, read
locally, with no credentials. Nothing else on disk carries it, so this tee
appends each observed ``rate_limits`` object to a local JSONL ledger that
``python -m token_tracker headroom`` reads.

Install it as the status line command, optionally wrapping the status line the
user already has. The wrapped command gets the same stdin and its stdout is
passed through byte for byte, along with its exit code::

    python /path/to/token_tracker/statusline_tee.py --command "~/.claude/statusline.sh"
    python /path/to/token_tracker/statusline_tee.py           # no wrapped command

With no wrapped command the tee prints a short ``5h 23% | 7d 41%`` line.

This file is stdlib-only and has no package-relative imports, so it runs as a
plain script from any working directory (Claude Code runs the status line in
the session's cwd). A failure to record never breaks the status line: the
wrapped command still runs and its output is still printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV_LIMITS_DIR = "TOKEN_TRACKER_LIMITS_DIR"
ENV_CLAUDE_FILE = "TOKEN_TRACKER_CLAUDE_STATUSLINE"
CLAUDE_FILE_NAME = "claude-statusline.jsonl"

# Keep the ledger bounded: past MAX_BYTES it is rewritten with the newest
# KEEP_LINES records. Only the newest snapshot per window is ever read.
MAX_BYTES = 1_000_000
KEEP_LINES = 500
WRAPPED_TIMEOUT_S = 30


def _is_windows() -> bool:
    return os.name == "nt"


def limits_dir() -> Path:
    """Where local limit ledgers live.

    ``TOKEN_TRACKER_LIMITS_DIR`` wins. Otherwise ``%LOCALAPPDATA%`` on Windows
    and ``$XDG_STATE_HOME`` (default ``~/.local/state``) elsewhere, each with
    ``ai-token-tracker/limits`` appended.
    """
    env = os.environ.get(ENV_LIMITS_DIR)
    if env:
        return Path(env).expanduser()
    if _is_windows():
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_STATE_HOME")
        root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "ai-token-tracker" / "limits"


def claude_ledger_path() -> Path:
    env = os.environ.get(ENV_CLAUDE_FILE)
    if env:
        return Path(env).expanduser()
    return limits_dir() / CLAUDE_FILE_NAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_record(payload: object, observed_at: str) -> dict | None:
    """The ledger line for one status line payload, or None when it has no limits."""
    if not isinstance(payload, dict):
        return None
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict) or not limits:
        return None
    return {
        "observed_at": observed_at,
        "session_id": payload.get("session_id"),
        "claude_version": payload.get("version"),
        "rate_limits": limits,
    }


def append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
    try:
        if path.stat().st_size > MAX_BYTES:
            _truncate(path)
    except OSError:
        pass


def _truncate(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines[-KEEP_LINES:]), encoding="utf-8", newline="")
    os.replace(tmp, path)


def summary_line(payload: object) -> str:
    """Short default status line when no command is wrapped."""
    if not isinstance(payload, dict):
        return ""
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return ""
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d"), ("spend_limit", "spend")):
        window = limits.get(key)
        if isinstance(window, dict):
            pct = window.get("used_percentage")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                parts.append(f"{label} {pct:.0f}%")
    return " | ".join(parts)


def _record_quietly(raw: bytes, path: Path, now_iso: str) -> None:
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "null")
    except ValueError:
        return
    record = build_record(payload, now_iso)
    if record is None:
        return
    try:
        append_record(path, record)
    except OSError as exc:  # never break the user's status line
        print(f"statusline_tee: could not record limits to {path}: {exc}", file=sys.stderr)


def _run_wrapped(raw: bytes, command: str | None, argv: list[str]) -> tuple[bytes, int]:
    if not command and not argv:
        return b"", 0
    try:
        completed = subprocess.run(
            command if command else argv,
            input=raw,
            stdout=subprocess.PIPE,
            shell=bool(command),
            timeout=WRAPPED_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"statusline_tee: wrapped command failed: {exc}", file=sys.stderr)
        return b"", 1
    return completed.stdout, completed.returncode


def main(
    argv: list[str] | None = None, *, stdin=None, stdout=None, now_iso: str | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog="statusline_tee",
        description="Record Claude Code status line rate_limits locally, then run the wrapped status line.",
    )
    parser.add_argument("--out", help=f"ledger file (default: {claude_ledger_path()})")
    parser.add_argument("--command", help="wrapped status line command, run through the shell")
    parser.add_argument(
        "wrapped", nargs=argparse.REMAINDER, help="wrapped command as argv, after `--` (no shell)"
    )
    args = parser.parse_args(argv)
    wrapped = list(args.wrapped)
    if wrapped and wrapped[0] == "--":
        wrapped = wrapped[1:]

    in_stream = stdin if stdin is not None else sys.stdin.buffer
    out_stream = stdout if stdout is not None else sys.stdout.buffer
    raw = in_stream.read()
    path = Path(args.out).expanduser() if args.out else claude_ledger_path()
    _record_quietly(raw, path, now_iso or _utc_now_iso())

    if args.command or wrapped:
        output, code = _run_wrapped(raw, args.command, wrapped)
        out_stream.write(output)
        out_stream.flush()
        return code
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "null")
    except ValueError:
        payload = None
    line = summary_line(payload)
    if line:
        out_stream.write((line + "\n").encode("utf-8"))
        out_stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
