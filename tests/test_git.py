import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.git import GitError, GitWorktreeManager, Worktree


def _worktree(branch: str = "agentops/test-12345678") -> Worktree:
    return Worktree(Path("C:/repo"), Path("C:/worktree"), branch, "main", "abc123")


class GitWorktreeTests(unittest.TestCase):
    @patch("agentops.git.Path.mkdir")
    @patch("agentops.git.subprocess.run")
    def test_creates_isolated_worktree(self, run, mkdir):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "C:/repo\n", ""),
            subprocess.CompletedProcess([], 0, "abc123\n", ""),
            subprocess.CompletedProcess([], 0, "main\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        manager = GitWorktreeManager()
        worktree = manager.create("C:/repo", "Add dark mode")
        self.assertEqual(worktree.repository, Path("C:/repo"))
        self.assertTrue(worktree.branch.startswith("agentops/add-dark-mode-"))
        self.assertEqual(worktree.base_branch, "main")
        self.assertEqual(worktree.base_commit, "abc123")
        mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("agentops.git.subprocess.run")
    def test_create_rejects_detached_head(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "C:/repo\n", ""),
            subprocess.CompletedProcess([], 0, "abc123\n", ""),
            subprocess.CompletedProcess([], 0, "\n", ""),
        ]
        with self.assertRaises(GitError):
            GitWorktreeManager().create("C:/repo", "Add dark mode")

    @patch("agentops.git.subprocess.run")
    def test_commit_skips_empty_worktree(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        worktree = _worktree()
        self.assertFalse(GitWorktreeManager().commit_changes(worktree, "message"))

    @patch("agentops.git.subprocess.run")
    def test_commit_raises_when_diff_inspection_fails(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 128, "", "fatal: bad revision"),
        ]
        with self.assertRaises(GitError):
            GitWorktreeManager().commit_changes(_worktree(), "message")
        self.assertEqual(run.call_count, 2)

    @patch("agentops.git.subprocess.run")
    def test_commit_raises_when_stage_fails(self, run):
        run.side_effect = [subprocess.CompletedProcess([], 1, "", "permission denied")]
        with self.assertRaises(GitError):
            GitWorktreeManager().commit_changes(_worktree(), "message")

    @patch("agentops.git.subprocess.run")
    def test_merge_refuses_dirty_base(self, run):
        run.side_effect = [subprocess.CompletedProcess([], 0, " M dirty.py\n", "")]
        with self.assertRaises(GitError):
            GitWorktreeManager().merge(_worktree())

    @patch("agentops.git.subprocess.run")
    def test_merge_refuses_changed_branch(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "\n", ""),
            subprocess.CompletedProcess([], 0, "other-branch\n", ""),
        ]
        with self.assertRaises(GitError):
            GitWorktreeManager().merge(_worktree())

    @patch("agentops.git.subprocess.run")
    def test_merge_refuses_moved_commit(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "\n", ""),
            subprocess.CompletedProcess([], 0, "main\n", ""),
            subprocess.CompletedProcess([], 0, "different-sha\n", ""),
        ]
        with self.assertRaises(GitError):
            GitWorktreeManager().merge(_worktree())

    @patch("agentops.git.subprocess.run")
    def test_remove_deletes_branch_after_worktree(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        GitWorktreeManager().remove(_worktree())
        self.assertEqual(run.call_count, 3)
        final = run.call_args_list[-1]
        self.assertIn("agentops/test-12345678", final.args[0])

    @patch("agentops.git.subprocess.run")
    def test_git_timeout_becomes_git_error(self, run):
        run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=120)
        with self.assertRaises(GitError):
            GitWorktreeManager().repository_root("C:/repo")

    def test_list_worktrees_parses_porcelain_edge_cases(self):
        manager = GitWorktreeManager()
        root = Path("C:/repo")
        managed = root / ".agentops" / "worktrees"
        porcelain = (
            "worktree C:/repo\nHEAD abc123\nbranch refs/heads/main\n\n"
            "worktree C:/repo/.agentops/worktrees/agentops-demo-12345678\n"
            "HEAD def456\nbranch refs/heads/agentops/demo-12345678\n\n"
            "worktree C:/repo with spaces/wt\nHEAD 789abc\ndetached"
        )
        manager.repository_root = lambda directory: root  # type: ignore[method-assign]
        manager._run = lambda repository, *args: subprocess.CompletedProcess([], 0, porcelain, "")  # type: ignore[method-assign]
        with patch.object(Path, "exists", return_value=True):
            worktrees = manager.list_worktrees(root)
        self.assertEqual(len(worktrees), 3)
        self.assertFalse(worktrees[0].managed)
        self.assertEqual(worktrees[0].branch, "main")
        self.assertTrue(worktrees[1].managed)
        self.assertEqual(worktrees[1].branch, "agentops/demo-12345678")
        self.assertIsNone(worktrees[2].branch)
        self.assertEqual(worktrees[2].head, "789abc")
        self.assertFalse(worktrees[2].managed)

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_managed_worktree_lifecycle(self):
        manager = GitWorktreeManager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "AgentOps Test"], cwd=root, check=True)
            (root / "file.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
            worktree = manager.create(root, "lifecycle check")
            try:
                listed = [info for info in manager.list_worktrees(root) if info.path == worktree.path.resolve()]
                self.assertEqual(len(listed), 1)
                self.assertTrue(listed[0].managed)
                inspected = manager.inspect_worktree(root, worktree.path)
                self.assertEqual(inspected["branch"], worktree.branch)
                self.assertEqual(str(inspected["status"]).strip(), "")
                (worktree.path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
                with self.assertRaises(GitError):
                    manager.cleanup_worktree(root, worktree.path)
                (worktree.path / "dirty.txt").unlink()
                result = manager.cleanup_worktree(root, worktree.path)
                self.assertTrue(result["removed_worktree"])
                self.assertTrue(result["deleted_branch"])
                self.assertFalse(worktree.path.exists())
            finally:
                if worktree.path.exists():
                    manager.cleanup_worktree(root, worktree.path, delete_unmerged_branch=True)
