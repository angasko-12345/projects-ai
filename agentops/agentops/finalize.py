"""Shared worktree finalization for every AgentOps entry point.

Committing, merging, and conflict-task creation previously lived in two
places (CLI and GUI controller) with subtly duplicated logic. Centralizing it
here keeps CLI, desktop, and any future entry point (REST API, evaluations)
on identical merge semantics: commit staged changes, merge only on success,
preserve the worktree and persist a debugging task on conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .git import GitError, GitWorktreeManager, Worktree, WorktreeRef
from .persistence import DegradationRecorder
from .state import StateStore
from .tasks import Task, utc_now


@dataclass(frozen=True)
class WorktreeFinalization:
    changed: bool
    merged: bool
    conflict_error: str | None


def record_worktree_provenance(
    state: StateStore,
    worktree: Worktree | None,
    workflow_id: str | None,
    degradation: DegradationRecorder | None = None,
) -> bool:
    """Persist worktree provenance once, for every entry point (A8).

    The CLI and the desktop client used to each build this row inline.  That
    duplication is why a lost write was a silent bare ``except: pass`` in both
    places at once: without the stored base commit, ``retry_merge`` validates
    against the base branch's current HEAD instead of the commit the workflow
    actually branched from.

    Returns True when the row was stored.  A failure is reported through
    ``degradation`` when one is supplied and never raised — losing provenance
    degrades a later merge retry, it does not make any recorded outcome false.
    """
    if worktree is None or workflow_id is None:
        return False
    try:
        state.record_worktree_ref(WorktreeRef(
            id=str(uuid4()),
            workflow_id=workflow_id,
            path=str(worktree.path),
            branch=worktree.branch,
            base_branch=worktree.base_branch,
            base_commit=worktree.base_commit,
            created_at=utc_now(),
        ))
    except Exception as error:
        if degradation is not None:
            degradation.record(
                "worktree_ref.create", error, workflow_id=workflow_id)
        return False
    return True


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
