"""Git worktree isolation for independently executed coding agents."""

from __future__ import annotations

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


class GitWorktreeManager:
    def _run(self, repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repository), *args], text=True, capture_output=True, check=False)

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
        slug = re.sub(r"[^a-z0-9]+", "-", task_name.lower()).strip("-")[:32] or "task"
        branch = f"agentops/{slug}-{uuid4().hex[:8]}"
        path = repository / ".agentops" / "worktrees" / branch.replace("/", "-")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(repository, "worktree", "add", "-b", branch, str(path), "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not create Git worktree.")
        return Worktree(repository, path, branch)

    def diff(self, worktree: Worktree) -> str:
        result = self._run(worktree.path, "diff", "HEAD")
        if result.returncode:
            raise GitError(result.stderr.strip())
        return result.stdout

    def commit_changes(self, worktree: Worktree, message: str) -> bool:
        self._run(worktree.path, "add", "-A")
        changed = self._run(worktree.path, "diff", "--cached", "--quiet")
        if changed.returncode == 0:
            return False
        result = self._run(worktree.path, "commit", "-m", message)
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not commit worktree changes.")
        return True

    def merge(self, worktree: Worktree) -> None:
        result = self._run(worktree.repository, "merge", "--no-ff", worktree.branch, "-m", f"agentops: merge {worktree.branch}")
        if result.returncode:
            raise GitError(result.stderr.strip() or "Git merge failed; resolve the conflict manually.")

    def remove(self, worktree: Worktree) -> None:
        result = self._run(worktree.repository, "worktree", "remove", "--force", str(worktree.path))
        if result.returncode:
            raise GitError(result.stderr.strip() or "Could not remove worktree.")
        self._run(worktree.repository, "worktree", "prune")
