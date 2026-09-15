"""Shared worktree finalization for every AgentOps entry point.

Committing, merging, and conflict-task creation previously lived in two
places (CLI and GUI controller) with subtly duplicated logic. Centralizing it
here keeps CLI, desktop, and any future entry point (REST API, evaluations)
on identical merge semantics: commit staged changes, merge only on success,
preserve the worktree and persist a debugging task on conflict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .git import GitError, GitWorktreeManager, Worktree
from .state import StateStore
from .tasks import Task


@dataclass(frozen=True)
class WorktreeFinalization:
    changed: bool
    merged: bool
    conflict_error: str | None


def finalize_worktree(
    manager: GitWorktreeManager,
    state: StateStore,
    worktree: Worktree,
    description: str,
    workflow_id: str,
    max_attempts: int,
) -> WorktreeFinalization:
    """Commit worktree changes and merge them into the base branch.

    Returns the outcome; on merge conflict the worktree is left in place and
    a persistent debugging task is recorded, mirroring long-standing CLI
    behavior. Raises GitError for commit/stage failures.
    """
    changed = manager.commit_changes(worktree, f"agentops: {description}")
    if not changed:
        return WorktreeFinalization(changed=False, merged=False, conflict_error=None)
    try:
        manager.merge(worktree)
    except GitError as error:
        state.add_task(Task(
            f"Resolve Git merge conflict for '{description}'.\n{error}", "debugging", workflow_id,
            max_attempts=max_attempts,
        ))
        return WorktreeFinalization(changed=True, merged=False, conflict_error=str(error))
    return WorktreeFinalization(changed=True, merged=True, conflict_error=None)
