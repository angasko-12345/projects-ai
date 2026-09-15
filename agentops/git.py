"""Git worktree isolation for independently executed coding agents."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    repository: Path
    path: Path
    branch: str
    base_branch: str
    base_commit: str


@dataclass(frozen=True)
class WorktreeInfo:
    repository: Path
    path: Path
    branch: str | None
    head: str
    managed: bool


class GitWorktreeManager:
    def _run(self, repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        try:
            return subprocess.run(["git", "-C", str(repository), *args], text=True, capture_output=True,
                                  check=False, timeout=120, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitError(f"Git command {' '.join(args)} failed: {error}") from error

    def repository_root(self, directory: str | Path) -> Path:
        result = self._run(Path(directory), "rev-parse", "--show-toplevel")
        if result.returncode:
            raise GitError(f"Not a Git repository: {result.stderr.strip()}")
        return Path(result.stdout.strip())

    def create(self, directory: str | Path, task_name: str) -> Worktree:
        repository = self.repository_root(directory)
        head = self._run(repository, "rev-parse", "--verify", "HEAD")
        if head.returncode:
            raise GitError("Cannot create an isolated worktree before the repository has an initial commit.")
        base_branch_result = self._run(repository, "branch", "--show-current")
        base_branch = base_branch_result.stdout.strip()
        if base_branch_result.returncode or not base_branch:
            raise GitError("Cannot merge an agent worktree from a detached Git HEAD.")
        base_commit = head.stdout.strip()
        slug = re.sub(r"[^a-z0-9]+", "-", task_name.lower()).strip("-")[:32] or "task"
        branch = f"agentops/{slug}-{uuid4().hex[:8]}"
        path = repository / ".agentops" / "worktrees" / branch.replace("/", "-")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(repository, "worktree", "add", "-b", branch, str(path), "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not create Git worktree.")
        return Worktree(repository, path, branch, base_branch, base_commit)

    def current_branch(self, repository: str | Path) -> str:
        result = self._run(Path(repository), "branch", "--show-current")
        branch = result.stdout.strip()
        if result.returncode or not branch:
            raise GitError("Cannot determine the current Git branch (detached HEAD?).")
        return branch

    def current_commit(self, repository: str | Path) -> str:
        result = self._run(Path(repository), "rev-parse", "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Cannot determine the current Git commit.")
        return result.stdout.strip()

    def _managed_root(self, repository: str | Path) -> Path:
        return self.repository_root(repository) / ".agentops" / "worktrees"

    def list_worktrees(self, repository: str | Path) -> list[WorktreeInfo]:
        root = self.repository_root(repository)
        result = self._run(root, "worktree", "list", "--porcelain")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not list Git worktrees.")
        managed_root = root / ".agentops" / "worktrees"
        worktrees: list[WorktreeInfo] = []
        path: Path | None = None
        head = ""
        branch: str | None = None

        def flush() -> None:
            if path is not None:
                resolved = path.resolve()
                worktrees.append(WorktreeInfo(
                    root, resolved, branch, head,
                    resolved.is_relative_to(managed_root.resolve()) if managed_root.exists() else False,
                ))

        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                flush()
                path = Path(line[len("worktree "):].strip())
                head = ""
                branch = None
            elif line.startswith("HEAD "):
                head = line[len("HEAD "):].strip()
            elif line.startswith("branch refs/heads/"):
                branch = line[len("branch refs/heads/"):].strip() or None
            elif not line.strip():
                flush()
                path = None
        flush()
        return worktrees

    def worktree_status(self, path: str | Path) -> str:
        result = self._run(Path(path), "status", "--porcelain")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not inspect worktree status.")
        return result.stdout

    def worktree_diff_stat(self, path: str | Path) -> str:
        result = self._run(Path(path), "diff", "--stat", "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not inspect worktree diff.")
        return result.stdout

    def inspect_worktree(self, repository: str | Path, path: str | Path) -> dict[str, object]:
        root = self.repository_root(repository)
        managed_root = self._managed_root(root)
        target = Path(path).resolve()
        if not target.is_relative_to(managed_root.resolve()):
            raise GitError(f"Refusing to manage a worktree outside {managed_root}: {path}")
        for info in self.list_worktrees(root):
            if info.path == target:
                return {
                    "repository": str(info.repository),
                    "path": str(info.path),
                    "branch": info.branch,
                    "head": info.head,
                    "managed": info.managed,
                    "status": self.worktree_status(target),
                    "diff_stat": self.worktree_diff_stat(target),
                }
        raise GitError(f"No registered Git worktree at: {path}")

    def cleanup_worktree(
        self,
        repository: str | Path,
        path: str | Path,
        delete_unmerged_branch: bool = False,
    ) -> dict[str, object]:
        info = self.inspect_worktree(repository, path)
        if str(info.get("status", "")).strip():
            raise GitError("Refusing to remove a worktree with uncommitted changes.")
        root = Path(str(info["repository"]))
        target = Path(str(info["path"]))
        removed = self._run(root, "worktree", "remove", "--force", str(target))
        if removed.returncode:
            raise GitError(removed.stderr.strip() or "Could not remove worktree.")
        self._run(root, "worktree", "prune")
        branch = info.get("branch")
        deleted_branch = False
        if isinstance(branch, str) and branch.startswith("agentops/"):
            deleted = self._run(root, "branch", "-d", branch)
            if deleted.returncode and delete_unmerged_branch:
                deleted = self._run(root, "branch", "-D", branch)
            if deleted.returncode:
                return {"removed_worktree": True, "deleted_branch": False, "branch": branch,
                        "detail": deleted.stderr.strip() or "Unmerged branch was preserved."}
            deleted_branch = True
        return {"removed_worktree": True, "deleted_branch": deleted_branch, "branch": branch}

    def diff(self, worktree: Worktree) -> str:
        result = self._run(worktree.path, "diff", "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip())
        return result.stdout

    def commit_changes(self, worktree: Worktree, message: str) -> bool:
        added = self._run(worktree.path, "add", "-A")
        if added.returncode:
            raise GitError(added.stderr.strip() or "Could not stage worktree changes.")
        changed = self._run(worktree.path, "diff", "--cached", "--quiet")
        if changed.returncode == 0:
            return False
        if changed.returncode != 1:
            # `git diff --quiet` reports 1 for differences; any other nonzero
            # status signals an inspection failure, not staged changes.
            raise GitError(changed.stderr.strip() or "Could not inspect staged worktree changes.")
        result = self._run(worktree.path, "commit", "-m", message)
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not commit worktree changes.")
        return True

    def merge(self, worktree: Worktree) -> None:
        clean = self._run(worktree.repository, "status", "--porcelain")
        if clean.returncode:
            raise GitError(clean.stderr.strip() or "Could not inspect base worktree status.")
        if clean.stdout.strip():
            raise GitError("Base worktree has uncommitted changes; refusing to merge agent worktree.")
        branch = self._run(worktree.repository, "branch", "--show-current")
        if branch.returncode or branch.stdout.strip() != worktree.base_branch:
            raise GitError(f"Base branch changed from '{worktree.base_branch}'; refusing to merge.")
        commit = self._run(worktree.repository, "rev-parse", "HEAD")
        if commit.returncode or commit.stdout.strip() != worktree.base_commit:
            raise GitError("Base commit changed while the agent worked; refusing to merge.")
        result = self._run(worktree.repository, "merge", "--no-ff", worktree.branch, "-m", f"agentops: merge {worktree.branch}")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Git merge failed; resolve the conflict manually.")

    def remove(self, worktree: Worktree) -> None:
        result = self._run(worktree.repository, "worktree", "remove", "--force", str(worktree.path))
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not remove worktree.")
        self._run(worktree.repository, "worktree", "prune")
        deleted = self._run(worktree.repository, "branch", "-d", worktree.branch)
        if deleted.returncode:
            raise GitError(deleted.stderr.strip() or f"Could not delete merged branch {worktree.branch}.")
