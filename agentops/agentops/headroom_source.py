"""I/O half of the headroom gate: read the tracker's JSON from a file or a command.

Kept apart from :mod:`agentops.headroom` so the evaluation rules stay a pure
leaf.  Every failure here becomes an unusable :class:`HeadroomSnapshot` with a
reason; nothing raises into routing.  No credential is read: the file or the
command output is the whole input.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .headroom import (
    SNAPSHOT_MISSING,
    SNAPSHOT_UNREADABLE,
    HeadroomBinding,
    HeadroomGate,
    HeadroomPolicy,
    HeadroomSnapshot,
    parse_snapshot,
)

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

CommandRunner = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]


def _default_command_runner(
    argv: Sequence[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    from .runtime import ProcessRuntime

    return subprocess.run(  # noqa: S603 - argv list from the user's own config, no shell
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        **ProcessRuntime.spawn_options(),
    )


class HeadroomFileReader:
    """Reads the document ``--out`` wrote (the tracker writes it atomically)."""

    def __init__(self, path: str | Path, max_age_seconds: float):
        self.path = Path(path)
        self.max_age_seconds = float(max_age_seconds)

    def __call__(self, now: float) -> HeadroomSnapshot:
        location = self.path.as_posix()
        try:
            if not self.path.is_file():
                return HeadroomSnapshot(SNAPSHOT_MISSING, f"headroom file not found: {location}")
            if self.path.stat().st_size > MAX_DOCUMENT_BYTES:
                return HeadroomSnapshot(SNAPSHOT_UNREADABLE, f"headroom file too large: {location}")
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return HeadroomSnapshot(
                SNAPSHOT_UNREADABLE, f"cannot read headroom file {location}: {type(error).__name__}"
            )
        return parse_snapshot(text, now, self.max_age_seconds)


class HeadroomCommandReader:
    """Runs ``headroom --json`` and caches the result for ``cache_seconds``."""

    def __init__(
        self,
        argv: Sequence[str],
        max_age_seconds: float,
        *,
        timeout_seconds: float = 30.0,
        cache_seconds: float = 60.0,
        runner: CommandRunner | None = None,
    ):
        self.argv = tuple(argv)
        self.max_age_seconds = float(max_age_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.cache_seconds = float(cache_seconds)
        self.runner = runner or _default_command_runner
        self._cached_at: float | None = None
        self._cached_text: str | None = None
        self._cached_failure: HeadroomSnapshot | None = None

    def _fetch(self) -> tuple[str | None, HeadroomSnapshot | None]:
        name = self.argv[0] if self.argv else "?"
        try:
            completed = self.runner(self.argv, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return None, HeadroomSnapshot(
                SNAPSHOT_UNREADABLE,
                f"headroom command {name} timed out after {self.timeout_seconds:g}s",
            )
        except OSError as error:
            return None, HeadroomSnapshot(
                SNAPSHOT_UNREADABLE,
                f"headroom command {name} could not start: {type(error).__name__}",
            )
        if completed.returncode != 0:
            return None, HeadroomSnapshot(
                SNAPSHOT_UNREADABLE, f"headroom command {name} exited {completed.returncode}"
            )
        text = completed.stdout or ""
        if len(text) > MAX_DOCUMENT_BYTES:
            return None, HeadroomSnapshot(
                SNAPSHOT_UNREADABLE, f"headroom command {name} output too large"
            )
        return text, None

    def __call__(self, now: float) -> HeadroomSnapshot:
        fresh = self._cached_at is not None and 0 <= now - self._cached_at < self.cache_seconds
        if not fresh:
            self._cached_text, self._cached_failure = self._fetch()
            self._cached_at = now
        if self._cached_failure is not None:
            return self._cached_failure
        # Re-validate on every call: a cached document still ages.
        return parse_snapshot(self._cached_text, now, self.max_age_seconds)


def build_headroom_gate(
    config: object | None, *, runner: CommandRunner | None = None
) -> HeadroomGate | None:
    """The gate for an ``AppConfig.headroom`` section, or None when it is absent."""
    if config is None:
        return None
    policy = HeadroomPolicy(
        reject_at_percent=config.reject_at_percent,
        deprioritize_at_percent=config.deprioritize_at_percent,
        unknown_policy=config.unknown_policy,
        max_age_seconds=config.max_age_seconds,
    )
    if config.file is not None:
        reader = HeadroomFileReader(config.file, config.max_age_seconds)
    else:
        reader = HeadroomCommandReader(
            config.command or (),
            config.max_age_seconds,
            timeout_seconds=config.command_timeout_seconds,
            cache_seconds=config.cache_seconds,
            runner=runner,
        )
    bindings = {
        agent: HeadroomBinding(provider, tuple(windows), tuple(limit_ids))
        for agent, (provider, windows, limit_ids) in config.agents.items()
    }
    return HeadroomGate(reader, bindings, policy)


__all__ = [
    "HeadroomCommandReader",
    "HeadroomFileReader",
    "MAX_DOCUMENT_BYTES",
    "build_headroom_gate",
]
