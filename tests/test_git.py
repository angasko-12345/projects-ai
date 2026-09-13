import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.git import GitWorktreeManager, Worktree


class GitWorktreeTests(unittest.TestCase):
    @patch("agentops.git.subprocess.run")
    def test_creates_isolated_worktree(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "C:/repo\n", ""),
            subprocess.CompletedProcess([], 0, "abc\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        manager = GitWorktreeManager()
        worktree = manager.create("C:/repo", "Add dark mode")
        self.assertEqual(worktree.repository, Path("C:/repo"))
        self.assertTrue(worktree.branch.startswith("agentops/add-dark-mode-"))

    @patch("agentops.git.subprocess.run")
    def test_commit_skips_empty_worktree(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        worktree = Worktree(Path("C:/repo"), Path("C:/worktree"), "agentops/test")
        self.assertFalse(GitWorktreeManager().commit_changes(worktree, "message"))
