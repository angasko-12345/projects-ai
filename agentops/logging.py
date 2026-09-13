"""File-backed execution logs, kept separate from Python's global logging setup."""

from __future__ import annotations

import re
from pathlib import Path

from .tasks import utc_now


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


class LogManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_run(self, task_id: str, agent: str, stdout: str, stderr: str, metadata: str) -> Path:
        task_root = self.root / _safe_name(task_id)
        task_root.mkdir(parents=True, exist_ok=True)
        stamp = _safe_name(utc_now())
        base = task_root / f"{stamp}-{_safe_name(agent)}"
        base.with_suffix(".stdout.log").write_text(stdout, encoding="utf-8")
        base.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8")
        metadata_path = base.with_suffix(".meta.log")
        metadata_path.write_text(metadata, encoding="utf-8")
        return metadata_path

    def list_logs(self, task_id: str | None = None) -> list[Path]:
        root = self.root / _safe_name(task_id) if task_id else self.root
        return sorted(root.rglob("*.log"), reverse=True) if root.exists() else []
