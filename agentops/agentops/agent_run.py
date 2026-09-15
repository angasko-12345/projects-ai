"""Persistent execution metadata for coding-agent subprocess runs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class AgentRunStatus(StrEnum):
    """Deterministic lifecycle states for one agent process invocation."""

    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    TERMINATED = "terminated"


TERMINAL_STATUSES = frozenset({
    AgentRunStatus.COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.TIMED_OUT,
    AgentRunStatus.TERMINATED,
})


def is_terminal(status: AgentRunStatus | str) -> bool:
    return (status if isinstance(status, AgentRunStatus) else AgentRunStatus(status)) in TERMINAL_STATUSES


class AgentRunRelationship(StrEnum):
    """Relationship of a run to an earlier attempt."""

    ROOT = "root"
    RETRY = "retry"
    REPAIR = "repair"


@dataclass(frozen=True)
class AgentRunContext:
    """Safe input used to create an AgentRun.

    The raw prompt is intentionally not persisted.  A hash and length provide
    correlation and diagnostics while avoiding secrets in task text.
    """

    agent: str | None = None
    executable: str | None = None
    role: str | None = None
    model: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    attempt: int = 1
    working_directory: str | None = None
    worktree: str | None = None
    prompt: str = ""
    command: tuple[str, ...] | None = None
    parent_run_id: str | None = None
    relationship: AgentRunRelationship = AgentRunRelationship.ROOT

    @property
    def prompt_metadata(self) -> dict[str, object]:
        return {
            "hash": hashlib.sha256(self.prompt.encode("utf-8", errors="replace")).hexdigest(),
            "length": len(self.prompt),
            # Deliberately no preview: prompts may contain credentials or
            # private repository details even when they are not labelled.
            "preview": None,
        }

    @property
    def safe_command(self) -> tuple[str, ...] | None:
        if self.command is None:
            return None
        return tuple(_redact_prompt(value, self.prompt) for value in self.command)

    @property
    def command_metadata(self) -> dict[str, object]:
        command = self.safe_command or ()
        return {
            "argv": list(command),
            "prompt_hash": self.prompt_metadata["hash"],
        }


@dataclass(frozen=True)
class AgentRunMetadata:
    """Optional metadata collected after a process finishes."""

    files_changed: tuple[str, ...] = ()
    diff_stat: str | None = None
    structured_result: object | None = None


@dataclass(frozen=True)
class AgentRunOutcome:
    """Final process outcome persisted on an AgentRun."""

    status: AgentRunStatus
    exit_code: int | None = None
    duration_seconds: float | None = None
    cancelled: bool = False
    timed_out: bool = False
    terminated: bool = False
    stdout_path: str | None = None
    stderr_path: str | None = None
    log_path: str | None = None
    files_changed: tuple[str, ...] = ()
    diff_stat: str | None = None
    error: str | None = None
    failure_classification: str | None = None
    structured_result: object | None = None


@dataclass(frozen=True)
class AgentRun:
    """Serializable domain object stored in the ``agent_runs`` table."""

    id: str
    agent: str | None
    status: AgentRunStatus
    workflow_id: str | None = None
    task_id: str | None = None
    attempt: int = 1
    executable: str | None = None
    role: str | None = None
    model: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    terminated: bool = False
    command: tuple[str, ...] | None = None
    command_metadata: dict[str, object] = field(default_factory=dict)
    working_directory: str | None = None
    worktree: str | None = None
    prompt_metadata: dict[str, object] = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    log_path: str | None = None
    files_changed: tuple[str, ...] = ()
    diff_stat: str | None = None
    error: str | None = None
    failure_classification: str | None = None
    relationship: AgentRunRelationship = AgentRunRelationship.ROOT
    parent_run_id: str | None = None
    retry_of: str | None = None
    repair_of: str | None = None
    structured_result: object | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def terminal(self) -> bool:
        return is_terminal(self.status)


class AgentRunObserver(Protocol):
    """Lifecycle sink used by the process runner.

    Keeping this as a small protocol lets the runner remain independent of
    SQLite while every application entry point can persist the same lifecycle.
    """

    def create_run(self, context: AgentRunContext) -> str | AgentRun: ...

    def mark_starting(self, run_id: str) -> None: ...

    def mark_running(self, run_id: str) -> None: ...

    def finish_run(self, run_id: str, outcome: AgentRunOutcome) -> None: ...

    def fail_run(self, run_id: str, error: BaseException, classification: str) -> None: ...


def _redact_prompt(value: str, prompt: str) -> str:
    if not prompt:
        return value
    return value.replace(prompt, "[REDACTED]")


def extract_structured_result(stdout: str) -> object | None:
    """Return a final JSON object/list from agent output when clearly present."""

    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


class GitRunMetadataCollector:
    """Best-effort, read-only collection of changed files and diff statistics."""

    def __call__(self, working_directory: str | Path) -> AgentRunMetadata:
        path = Path(working_directory)
        files: list[str] = []
        diff_stat: str | None = None
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=path,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            if status.returncode == 0:
                for line in status.stdout.splitlines():
                    if len(line) < 4:
                        continue
                    name = line[3:].strip()
                    if name.startswith("-> "):
                        name = name[3:].strip()
                    if name:
                        files.append(name)
        except (OSError, subprocess.SubprocessError):
            files = []
        try:
            stat = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                cwd=path,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            if stat.returncode == 0 and stat.stdout.strip():
                diff_stat = stat.stdout
        except (OSError, subprocess.SubprocessError):
            diff_stat = None
        return AgentRunMetadata(tuple(files), diff_stat)


__all__ = [
    "TERMINAL_STATUSES",
    "is_terminal",
    "AgentRun",
    "AgentRunContext",
    "AgentRunMetadata",
    "AgentRunObserver",
    "AgentRunOutcome",
    "AgentRunRelationship",
    "AgentRunStatus",
    "GitRunMetadataCollector",
    "extract_structured_result",
]
