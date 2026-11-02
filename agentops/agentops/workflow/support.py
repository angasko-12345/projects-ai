"""Shared workflow types, constants and small helpers."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunObserver,
    AgentRunOutcome,
)
from ..logging import redact_text


def _scrub_structured(value: object) -> object:
    """Recursively redact secret-looking strings in persisted evidence.

    Callers must still avoid putting raw prompts or credentials into evidence;
    this is defense-in-depth for token patterns in arbitrary mappings.
    """
    try:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {key: _scrub_structured(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_scrub_structured(item) for item in value]
    except Exception:
        return "[UNREPRESENTABLE]"
    return value


class _RecordingObserver:
    """Delegate that reports whether the runner persisted a run."""

    def __init__(self, delegate: AgentRunObserver):
        self._delegate = delegate
        self.created_run_id: str | None = None

    def create_run(self, context: AgentRunContext) -> str | AgentRun:
        created = self._delegate.create_run(context)
        self.created_run_id = created.id if isinstance(created, AgentRun) else str(created)
        return created

    def mark_starting(self, run_id: str) -> None:
        self._delegate.mark_starting(run_id)

    def mark_running(self, run_id: str) -> None:
        self._delegate.mark_running(run_id)

    def finish_run(self, run_id: str, outcome: AgentRunOutcome) -> None:
        self._delegate.finish_run(run_id, outcome)

    def fail_run(self, run_id: str, error: BaseException, classification: str) -> None:
        self._delegate.fail_run(run_id, error, classification)


@dataclass(frozen=True)
class WorkflowResult:
    workflow_id: str
    ready: bool
    summary: str


# Task roles created by create_standard_workflow, in dependency order.
# Single source of truth: the GUI controller pins explicit agent preferences
# against this tuple when an operator chooses a fixed agent for a task.
def _is_git_working_tree(path: str | Path) -> bool:
    """True when ``path`` is inside a git working tree.

    GitRunMetadataCollector reads `git status --porcelain`, which prints nothing
    outside a repository. Without this check an empty result is ambiguous
    between "nothing changed" and "there was nothing to look at".
    """
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "--is-inside-work-tree"),
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        return False
    return completed.returncode == 0 and "true" in (completed.stdout or "").lower()


STANDARD_TASK_ROLES = ("architecture", "implementation", "verification", "review")


#: Per-dependency and total character budget for the handoff block in a
#: dependent agent's prompt. Dependency results are transcripts that grow with
#: the work, so without a ceiling each extra generation inflates the next prompt.
HANDOFF_RESULT_CHARS = 2000


HANDOFF_TOTAL_CHARS = 8000
