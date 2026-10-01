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
        launch_phase2_process,
        load_eval_model,
        stop,
    )
    import training.external_experiment as _xp

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

class _FakeProc:
    """Minimal Popen stand-in: records terminate/kill/wait calls."""

    def __init__(self, alive_at_poll=True):
        self.terminated = 0
        self.killed = 0
        self.waits = 0
        self.returncode = None if alive_at_poll else 3
        self._log_closed = 0
        log = self

        class _Log:
            def close(self):
                log._log_closed += 1

        self._log_file = _Log()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = 0

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        self.waits += 1
        return self.returncode


@unittest.skipUnless(_HAS_DEPS, "torch not installed")
class TestLaunchCleanup(unittest.TestCase):
    def _patch(self, **kw):
        old = {k: getattr(_xp, k) for k in kw}
        for k, v in kw.items():
            setattr(_xp, k, v)
        self.addCleanup(lambda: [setattr(_xp, k, v) for k, v in old.items()])

    def test_attach_failure_stops_proc(self):
        proc = _FakeProc()
        stopped = []
        self._patch(launch_game=lambda *a, **k: proc,
                     wait_attach=lambda title, timeout_s=20.0: (_ for _ in ()).throw(
                         RuntimeError("no window")),
                     check_alive=lambda p, phase: None,
                     stop=lambda p: stopped.append(p))
        with self.assertRaises(RuntimeError):
            launch_phase2_process("T", 0, 60)
        self.assertEqual(stopped, [proc])

    def test_liveness_failure_stops_proc(self):
        proc = _FakeProc()
        stopped = []
        self._patch(launch_game=lambda *a, **k: proc,
                     wait_attach=lambda title, timeout_s=20.0: None,
                     check_alive=lambda p, phase: (_ for _ in ()).throw(
                         RuntimeError("exited")),
                     stop=lambda p: stopped.append(p))
        with self.assertRaises(RuntimeError):
            launch_phase2_process("T", 0, 60)
        self.assertEqual(stopped, [proc])

    def test_successful_launch_hands_off_cleanup(self):
        proc = _FakeProc()
        self._patch(launch_game=lambda *a, **k: proc,
                     wait_attach=lambda title, timeout_s=20.0: None,
                     check_alive=lambda p, phase: None,
                     stop=stop)
        out = launch_phase2_process("T", 0, 60)
        self.assertIs(out, proc)
        self.assertEqual(proc.terminated, 0)  # no cleanup before handoff
        stop(out)  # caller cleanup
        self.assertEqual(proc.terminated, 1)
        stop(out)  # idempotent second call
        self.assertEqual(proc.terminated, 1)

    def test_stop_idempotent(self):
        proc = _FakeProc()
        stop(proc)
        stop(proc)
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc._log_closed, 1)

    def test_load_eval_model_closes_env(self):
        closed = []
        sentinel = object()

        class _Env:
            def close(self):
                closed.append(True)

        made = []

        def make_env():
            made.append(True)
            return _Env()

        class _Trainer:
            model = sentinel

        self._patch(PPOTrainer=type("P", (), {"load_checkpoint":
                                             staticmethod(lambda *a, **k: _Trainer())}))
        self.assertIs(load_eval_model("ckpt.pt", make_env), sentinel)
        self.assertEqual(len(made), 1)
        self.assertEqual(closed, [True])

    def test_load_eval_model_closes_env_on_failure(self):
        closed = []

        class _Env:
            def close(self):
                closed.append(True)

        def make_env():
            return _Env()

        class _Boom:
            @staticmethod
            def load_checkpoint(*a, **k):
                raise RuntimeError("corrupt checkpoint")

        self._patch(PPOTrainer=_Boom)
        with self.assertRaises(RuntimeError):
            load_eval_model("ckpt.pt", make_env)
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
