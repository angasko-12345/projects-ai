"""AgentOpsController.workflow_diff against a real temporary Git repository."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.git import GitError, GitWorktreeManager, WorktreeRef
from agentops.gui_controller import AgentOpsController
from agentops.state import StateStore


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class WorkflowDiffTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        _git(self.root, "init", "-q", "-b", "main")
        (self.root / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        _git(self.root, "add", "app.py")
        _git(self.root, "commit", "-q", "-m", "init")
        self.base = _git(self.root, "rev-parse", "HEAD").strip()
        self.state = StateStore(self.root / ".agentops" / "state.sqlite")
        self.addCleanup(self.state.close)
        self.workflow = self.state.create_workflow("Change app")
        self.controller = AgentOpsController()

    def _worktree(self) -> Path:
        worktree = GitWorktreeManager().create(self.root, "change app")
        (worktree.path / "app.py").write_text("a = 1\nb = 3\n", encoding="utf-8")
        self._record(str(worktree.path), worktree.branch)
        return worktree.path

    def _record(self, path: str, branch: str) -> None:
        self.state.record_worktree_ref(
            WorktreeRef("", self.workflow, path, branch, "main", self.base, "")
        )

    def _diff(self, **kwargs) -> dict[str, object]:
        return self.controller.workflow_diff(self.root, self.workflow, **kwargs)

    def test_live_worktree_diffs_uncommitted_edits_against_base(self):
        path = self._worktree()
        diff = self._diff()
        self.assertEqual(diff["source"], "worktree")
        self.assertIn("-b = 2\n+b = 3\n", diff["text"])
        self.assertFalse(diff["truncated"])
        self.assertEqual((diff["base_commit"], diff["path"]), (self.base, str(path)))
        self.assertEqual(
            sorted(diff), ["base_commit", "branch", "path", "source", "text", "truncated"]
        )

    def test_branch_only_after_the_worktree_is_removed(self):
        path = self._worktree()
        _git(path, "commit", "-q", "-am", "edit")
        _git(self.root, "worktree", "remove", "--force", str(path))
        diff = self._diff()
        self.assertEqual(diff["source"], "branch")
        self.assertIn("+b = 3", diff["text"])
        self.assertTrue(str(diff["branch"]).startswith("agentops/"))

    def test_stat_fallback_uses_newest_run_with_changes(self):
        self._record(str(self.root / ".agentops" / "worktrees" / "gone"), "agentops/gone")
        for stat in ("1 file changed, 1 insertion(+)", None):
            run = self.state.create_agent_run(AgentRunContext(agent="codex", workflow_id=self.workflow))
            files = ("app.py",) if stat else ()
            self.state.finish_agent_run(
                run.id, AgentRunOutcome(AgentRunStatus.COMPLETED, 0, files_changed=files, diff_stat=stat)
            )
        diff = self._diff()
        self.assertEqual(diff["source"], "stat")
        self.assertEqual(diff["text"], "1 file changed, 1 insertion(+)")
        self.assertEqual(diff["branch"], "agentops/gone")

    def test_no_provenance_and_no_runs_is_an_empty_stat(self):
        diff = self._diff()
        self.assertEqual((diff["source"], diff["text"], diff["path"]), ("stat", "", None))

    def test_truncates_at_a_line_boundary(self):
        self._worktree()
        full = self._diff()["text"]
        diff = self._diff(max_bytes=40)
        self.assertTrue(diff["truncated"])
        self.assertLessEqual(len(diff["text"].encode()), 40)
        self.assertTrue(full.startswith(diff["text"]) and diff["text"].endswith("\n"))
        with self.assertRaises(ValueError):
            self._diff(max_bytes=0)

    def test_refuses_a_recorded_path_outside_the_managed_root(self):
        outside = self.root.parent / f"{self.root.name}-outside"
        self.addCleanup(shutil.rmtree, outside, True)
        _git(self.root, "worktree", "add", "-q", "-b", "agentops/outside", str(outside))
        self._record(str(outside), "agentops/outside")
        with self.assertRaisesRegex(GitError, "outside"):
            self._diff()


if __name__ == "__main__":
    unittest.main()
