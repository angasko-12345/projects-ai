"""File-backed execution logs, kept separate from Python's global logging setup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .tasks import utc_now


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


def redact_text(value: str) -> str:
    """Redact secret-looking tokens from text (public API; ``_redact`` is an alias)."""
    value = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)([^\s'\"]+)", r"\1[REDACTED]", value)
    return re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,})\b", "[REDACTED]", value)


def _redact(value: str) -> str:
    return redact_text(value)


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


@dataclass(frozen=True)
class RunLogArtifacts:
    stdout_path: Path
    stderr_path: Path
    metadata_path: Path


class LogManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        _restrict(self.root, 0o700)

    def write_run_artifacts(
        self,
        task_id: str,
        agent: str,
        stdout: str,
        stderr: str,
        metadata: str,
    ) -> RunLogArtifacts:
        task_root = self.root / _safe_name(task_id)
        task_root.mkdir(parents=True, exist_ok=True)
        _restrict(task_root, 0o700)
        stamp = _safe_name(utc_now())
        base = task_root / f"{stamp}-{_safe_name(agent)}"
        stdout_path = base.with_suffix(".stdout.log")
        stderr_path = base.with_suffix(".stderr.log")
        metadata_path = base.with_suffix(".meta.log")
        stdout_path.write_text(_redact(stdout), encoding="utf-8")
        stderr_path.write_text(_redact(stderr), encoding="utf-8")
        metadata_path.write_text(_redact(metadata), encoding="utf-8")
        for path in (stdout_path, stderr_path, metadata_path):
            _restrict(path, 0o600)
        return RunLogArtifacts(stdout_path, stderr_path, metadata_path)

    def write_run(self, task_id: str, agent: str, stdout: str, stderr: str, metadata: str) -> Path:
        """Write a run and return its metadata path for backward compatibility."""
        return self.write_run_artifacts(task_id, agent, stdout, stderr, metadata).metadata_path

    def list_logs(self, task_id: str | None = None) -> list[Path]:
        root = self.root / _safe_name(task_id) if task_id else self.root
        return sorted(root.rglob("*.log"), reverse=True) if root.exists() else []

    def read_tail(self, path: str | Path, max_bytes: int = 65536) -> tuple[str, bool]:
        """Read the tail of a log file contained in this log root.

        Returns the decoded text and whether older content was truncated.
        """
        if not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("Log read size must be a positive integer.")
        root = self.root.resolve()
        target = (root / Path(path)).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Refusing to read a log outside {root}: {path}")
        if not target.is_file():
            raise FileNotFoundError(f"Log file does not exist: {target}")
        data = target.read_bytes()
        truncated = len(data) > max_bytes
        return data[-max_bytes:].decode("utf-8", errors="replace"), truncated
