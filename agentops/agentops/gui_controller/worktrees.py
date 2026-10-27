"""AgentOpsController: git worktrees, manual merge retry and worktree provenance."""

from __future__ import annotations

from pathlib import Path

from .. import gui_controller as _ns


class WorktreesMixin:
    """Git worktrees, manual merge retry and worktree provenance."""

    def list_worktrees(self, directory: str | Path) -> list[dict[str, object]]:
        root = self._operation_root(directory)
        manager = _ns.GitWorktreeManager()
        entries: list[dict[str, object]] = []
        for info in manager.list_worktrees(root):
            try:
                status = manager.worktree_status(info.path)
            except _ns.GitError:
                status = "(status unavailable)"
            entries.append(
                {
                    "repository": str(info.repository),
                    "path": str(info.path),
                    "branch": info.branch,
                    "head": info.head,
                    "managed": info.managed,
                    "status": status,
                }
            )
        return entries

    def inspect_worktree(self, directory: str | Path, path: str | Path) -> dict[str, object]:
        root = self._operation_root(directory)
        return _ns.GitWorktreeManager().inspect_worktree(root, path)

    def cleanup_worktree(
        self,
        directory: str | Path,
        path: str | Path,
        delete_unmerged_branch: bool = False,
    ) -> dict[str, object]:
        root = self._operation_root(directory)
        return _ns.GitWorktreeManager().cleanup_worktree(root, path, delete_unmerged_branch)

    def retry_merge(self, directory: str | Path, path: str | Path) -> dict[str, object]:
        """Re-attempt a merge for a preserved worktree (manual recovery path).

        This is an explicit operator action, not a readiness decision, so it
        deliberately does not run the READY contract: the workflow that
        produced the worktree may no longer exist in state, and the operator is
        choosing to merge now. The normal flow already gates merges on
        ``result.ready`` (see ``_run_operation`` and ``cli.run``).

        The safety gates here are the ones that still matter for a blind
        re-merge: the worktree must be clean, must be a managed ``agentops/*``
        branch, and must merge against the stored base branch/commit when
        provenance exists, so a moved base is still refused by ``merge()``.
        """
        root = self._operation_root(directory)
        manager = _ns.GitWorktreeManager()
        info = manager.inspect_worktree(root, path)
        if str(info.get("status", "")).strip():
            raise _ns.GitError("Refusing to retry a merge with uncommitted worktree changes.")
        branch = info.get("branch")
        if not isinstance(branch, str) or not branch.startswith("agentops/"):
            raise _ns.GitError("Retry is only supported for managed agentops/* worktree branches.")
        state = _ns.StateStore(self._state_path(root))
        try:
            stored = state.find_worktree_ref_by_path(str(info["path"]))
        finally:
            state.close()
        if stored is not None:
            # Stored provenance wins: the retry must target the original base
            # branch/commit, not whatever HEAD happens to be now.
            worktree = _ns.Worktree(
                root, Path(str(info["path"])), stored.branch, stored.base_branch, stored.base_commit
            )
        else:
            worktree = _ns.Worktree(
                root,
                Path(str(info["path"])),
                branch,
                manager.current_branch(root),
                manager.current_commit(root),
            )
        manager.merge(worktree)
        return {
            "merged": True,
            "path": str(info["path"]),
            "branch": worktree.branch,
            "base_branch": worktree.base_branch,
            "used_stored_provenance": stored is not None,
        }

    def get_worktree_ref(self, directory: str | Path, workflow_id: str) -> dict[str, object] | None:
        """Phase 3: expose persisted provenance for one workflow."""
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            ref = state.get_worktree_ref(workflow_id)
            return _ns.serialize_worktree_ref(ref) if ref is not None else None
        finally:
            state.close()

    def list_worktree_refs(
        self, directory: str | Path, workflow_id: str | None = None
    ) -> list[dict[str, object]]:
        """Phase 3: list persisted provenance records."""
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            return [
                _ns.serialize_worktree_ref(ref) for ref in state.list_worktree_refs(workflow_id)
            ]
        finally:
            state.close()
