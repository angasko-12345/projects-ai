"""Shared worktree finalization for every AgentOps entry point.

Committing, merging, and conflict-task creation previously lived in two
places (CLI and GUI controller) with subtly duplicated logic. Centralizing it
here keeps CLI, desktop, and any future entry point (REST API, evaluations)
on identical merge semantics: commit staged changes, merge only on success,
preserve the worktree and persist a debugging task on conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .failure import FailureCategory, FailureClassifier, FailureSource
from .git import GitError, GitWorktreeManager, Worktree, WorktreeRef
from .persistence import DegradationRecorder
from .state import StateStore
from .tasks import Task, TaskStatus, utc_now


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
        # The repair task must name the actual cause: only a real conflict
        # is something to resolve by merging.
        category = FailureClassifier.classify(
            source=FailureSource.GIT, error=str(error)).category
        if category is FailureCategory.GIT_CONFLICT:
            headline = f"Resolve Git merge conflict for '{description}'."
        elif category is FailureCategory.DIRTY_WORKTREE:
            headline = f"Clean the dirty base worktree before merging '{description}'."
        elif category is FailureCategory.POLICY_VIOLATION:
            headline = f"Git merge for '{description}' was refused; re-validate the base branch and retry."
        else:
            headline = f"Git merge failed for '{description}'; inspect the error and resolve."
        state.add_task(Task(
            f"{headline}\n{error}", "debugging", workflow_id,
            max_attempts=max_attempts,
        ))
        return WorktreeFinalization(changed=True, merged=False, conflict_error=str(error))
    return WorktreeFinalization(changed=True, merged=True, conflict_error=None)


def implementation_passed(state: StateStore, workflow_id: str) -> bool:
    """True when the implementation task itself reached PASSED.

    Part of the merge rule, so it lives here rather than in ``cli``: both the
    CLI and the desktop controller need it, and a controller importing the CLI
    module for a predicate is the wrong direction of dependency.
    """
    try:
        tasks = state.list_tasks(workflow_id)
    except Exception:
        return False
    return any(task.role == "implementation" and task.status is TaskStatus.PASSED
               for task in tasks)

def finalize_for_outcome(
    manager: GitWorktreeManager,
    state: StateStore,
    worktree: Worktree,
    description: str,
    workflow_id: str,
    max_attempts: int,
    *,
    ready: bool,
) -> WorktreeFinalization:
    """Finalize a worktree according to the ONE authoritative merge rule.

    Every entry point (CLI, desktop, anything added later) must call this
    rather than re-deriving the gate. The two call sites previously disagreed:
    the CLI merged whenever the implementation task passed, while the desktop
    client merged only on ``result.ready``. Same run, same work, different
    outcome depending on how it was invoked.

    The rule, per the operator's decision:

      READY              -> commit and merge
      not READY          -> commit and PRESERVE the worktree, never merge

    Two distinct authorities, deliberately not conflated:

      READY is the SOLE automatic merge authority. Nothing else reaches merge().
      implementation_passed decides only whether the work is PRESERVED.

    implementation status    preservation    automatic merge
    -------------------------------------------------------
    PASSED                    yes             only if READY
    FAILED                    yes             never

    A commit here is a checkpoint, not a correctness claim: it records "this is
    the state produced by this attempt, preserved with provenance so it can be
    inspected, repaired, or retried." The irreversible action is merge; commit
    is the reversible evidence-preservation mechanism. That is why a FAILED
    implementation is still committed -- dropping it would discard recoverable
    partial work, which is the mistake this design exists to prevent.

    Unverified work is not discarded and not merged. It stays in the worktree
    so it can be merged later with ``retry_merge_for_worktree`` once
    verification is unblocked. Merging on "the implementation exited 0" was the
    defect: it could not distinguish a FAILED verification from an UNVERIFIED
    one, so a demonstrated failure and a merely-unproven change both merged.
    """
    if not ready:
        # Limbo: commit so nothing is lost, but do not merge. The worktree is
        # left in place and the caller reports where it is.
        changed = manager.commit_changes(worktree, f"agentops: {description}")
        return WorktreeFinalization(changed=changed, merged=False,
                                    conflict_error=None)
    return finalize_worktree(manager, state, worktree, description,
                             workflow_id, max_attempts)


def retry_merge_for_worktree(
    manager: GitWorktreeManager,
    state: StateStore,
    root: Path,
    path: Path,
) -> dict[str, object]:
    """Merge a preserved worktree on explicit operator request.

    Deliberately does not run the READY contract: this is a person deciding
    now, not the workflow deciding for them, and the originating workflow may
    no longer exist in state. The safety gates that still matter for a blind
    re-merge are kept: the worktree must be clean, must be a managed
    ``agentops/*`` branch, and must merge against the stored base branch and
    commit when provenance exists, so a moved base is still refused.
    """
    info = manager.inspect_worktree(root, path)
    if str(info.get("status", "")).strip():
        raise GitError("Refusing to retry a merge with uncommitted worktree changes.")
    branch = info.get("branch")
    if not isinstance(branch, str) or not branch.startswith("agentops/"):
        raise GitError("Retry is only supported for managed agentops/* worktree branches.")
    stored = state.find_worktree_ref_by_path(str(info["path"]))
    if stored is not None:
        # Stored provenance wins: retry must target the original base, not
        # whatever HEAD happens to be now.
        worktree = Worktree(root, Path(str(info["path"])), stored.branch,
                            stored.base_branch, stored.base_commit)
    else:
        worktree = Worktree(root, Path(str(info["path"])), branch,
                            manager.current_branch(root), manager.current_commit(root))
    manager.merge(worktree)
    return {"merged": True, "path": str(info["path"]), "branch": worktree.branch,
            "base_branch": worktree.base_branch,
            "used_stored_provenance": stored is not None}
