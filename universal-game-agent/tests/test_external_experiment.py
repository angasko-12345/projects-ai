"""Tests for external-experiment run isolation helpers (no display needed)."""
import os
import unittest
from pathlib import Path

try:
    from training.external_experiment import (
        _apply_run_title,
        _results_path,
        _run_checkpoint_dir,
        _unique_title,
    )

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "torch not installed")
class TestRunIsolation(unittest.TestCase):
    def test_unique_title(self):
        title = _unique_title("Game")
        self.assertTrue(title.startswith("Game-"))
        self.assertTrue(title.endswith(str(os.getpid())))

    def test_checkpoint_dir_isolated(self):
        out = _run_checkpoint_dir({"checkpoint_dir": "checkpoints/x"})
        self.assertEqual(out, str(Path("checkpoints/x") / f"run-{os.getpid()}"))

    def test_results_path_isolated(self):
        out = _results_path(Path("experiments/exp.yaml"))
        self.assertEqual(out.name, f"exp-{os.getpid()}_results.json")
        self.assertEqual(out.parent, Path("experiments"))

    def test_apply_run_title_window(self):
        cfg = {"capture": {"mode": "window", "title": "Old"},
               "lifecycle": {"mode": "window", "title": "Old"}}
        out = _apply_run_title(cfg, "New-123")
        self.assertEqual(out["capture"]["title"], "New-123")
        self.assertEqual(out["lifecycle"]["title"], "New-123")
        self.assertEqual(cfg["capture"]["title"], "Old")  # input not mutated

    def test_apply_run_title_leaves_other_modes(self):
        cfg = {"capture": {"mode": "region"}, "lifecycle": {"mode": "none"}}
        out = _apply_run_title(cfg, "New-123")
        self.assertNotIn("title", out["capture"])
        self.assertNotIn("title", out["lifecycle"])


if __name__ == "__main__":
    unittest.main()
