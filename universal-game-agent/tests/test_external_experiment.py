"""Tests for external-experiment run isolation helpers (no display needed)."""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import os
import unittest
from pathlib import Path

try:
    from training.external_experiment import (
        _apply_run_title,
        _resume_trainer,
        _results_path,
        _run_checkpoint_dir,
        _unique_title,
        launch_game,
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

    def test_launch_game_popen_failure_closes_log(self):
        import subprocess
        import tempfile
        from unittest.mock import patch

        import os as _os

        old_cwd = _os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            _os.chdir(tmp)
            try:
                opened = []
                real_open = open

                def spy_open(*args, **kwargs):
                    fh = real_open(*args, **kwargs)
                    opened.append(fh)
                    return fh

                with patch("builtins.open", spy_open), \
                        patch.object(subprocess, "Popen",
                                     side_effect=OSError("cannot fork")):
                    with self.assertRaises(OSError):
                        launch_game("T", 0, 60, "phase9")
                self.assertEqual(len(opened), 1)
                self.assertTrue(opened[0].closed)
            finally:
                _os.chdir(old_cwd)

    def _ckpt_trainer(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        class _Trainer:
            config = type("C", (), {"checkpoint_dir": tmp.name})()

            def save_checkpoint(self, path):
                Path(path).write_bytes(b"ckpt")

        return _Trainer()

    def test_resume_trainer_make_env_failure_propagates(self):
        def boom_env():
            raise RuntimeError("no display")

        with self.assertRaises(RuntimeError):
            _resume_trainer(self._ckpt_trainer(), boom_env)

    def test_resume_trainer_load_failure_closes_env(self):
        closed = []

        class _Env:
            def close(self):
                closed.append(True)

        class _Boom:
            @staticmethod
            def load_checkpoint(*args, **kwargs):
                raise RuntimeError("corrupt checkpoint")

        self._patch(PPOTrainer=_Boom)
        with self.assertRaises(RuntimeError):
            _resume_trainer(self._ckpt_trainer(), _Env)
        self.assertEqual(closed, [True])

    def test_resume_trainer_success_keeps_env_open(self):
        closed = []
        sentinel = object()

        class _Env:
            def close(self):
                closed.append(True)

        class _Ok:
            model = sentinel

        self._patch(PPOTrainer=type("P", (), {"load_checkpoint":
                                             staticmethod(lambda *a, **k: _Ok())}))
        out = _resume_trainer(self._ckpt_trainer(), _Env)
        self.assertIsInstance(out, _Ok)
        self.assertEqual(closed, [])  # ownership transferred, not closed


@unittest.skipUnless(_HAS_DEPS, "torch not installed")
class TestFailureResults(unittest.TestCase):
    def test_experiment_failure_writes_results_json(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "exp.yaml"
            cfg_path.write_text("env: {}\n", encoding="utf-8")  # no "game" key
            with self.assertRaises(KeyError):
                _xp.run_external_experiment(cfg_path)
            out = _results_path(cfg_path)
            self.assertTrue(out.is_file())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "failed")
            self.assertIn("game", data["error"])
            self.assertEqual(data["config_file"], str(cfg_path))


_CONFIG_YAML = (
    "game:\n"
    "  title: ExternPongExp\n"
    "  fps: 60\n"
    "env: {}\n"
    "model: {}\n"
    "ppo:\n"
    "  seed: 0\n"
    "eval:\n"
    "  episodes: 1\n"
)


class _FakeEnv:
    """Env stand-in exposing only what the driver's probe touches."""

    def __init__(self):
        self.action_space = type("_Space", (), {"n": 3})()
        self.closed = 0

    def close(self):
        self.closed += 1


@unittest.skipUnless(_HAS_DEPS, "torch not installed")
class TestAttachFailureResults(unittest.TestCase):
    """ROOT-027: an attach failure must leave a results record and stop the game.

    Every phase dependency is stubbed on `training.external_experiment`; the
    only real code that runs is `run_external_experiment` and the body of
    `_run_external_experiment` up to the phase-1 attach.
    """

    def _patch(self, **kw):
        old = {k: getattr(_xp, k) for k in kw}
        for k, v in kw.items():
            setattr(_xp, k, v)
        self.addCleanup(lambda: [setattr(_xp, k, v) for k, v in old.items()])

    def _stub_pre_attach_phase(self, attach_error, windows, stopped):
        """Patch everything the driver touches before and during phase 1 launch.

        `windows` collects the stale-window probe attachments so a test can
        prove the driver really reached line 322's attach site; `stopped`
        collects the process handles the driver hands to `stop()`.
        """
        proc = _FakeProc()

        def window_manager(title):
            def attach():
                windows.append(title)
                raise _xp.WindowNotFoundError(f"no window titled {title!r}")
            return type("_WM", (), {"attach": staticmethod(attach)})()

        self._patch(
            make_external_env_from_config=lambda env_cfg: _FakeEnv,
            WindowManager=window_manager,
            launch_game=lambda *a, **k: proc,
            wait_attach=lambda title, timeout_s=20.0: (_ for _ in ()).throw(
                attach_error),
            check_alive=lambda p, phase: None,
            stop=lambda p: stopped.append(p),
        )
        return proc

    def _config_in_tempdir(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg_path = Path(tmp.name) / "exp.yaml"
        cfg_path.write_text(_CONFIG_YAML, encoding="utf-8")
        return cfg_path

    def test_attach_failure_writes_results_and_stops_game(self):
        import json

        stopped = []
        windows = []
        error = RuntimeError("game window 'ExternPongExp-1' did not appear within 20 s")
        cfg_path = self._config_in_tempdir()
        proc = self._stub_pre_attach_phase(error, windows, stopped)

        with self.assertRaises(RuntimeError) as ctx:
            _xp.run_external_experiment(cfg_path)

        # the original exception still reaches the caller
        self.assertIs(ctx.exception, error,
                      f"run_external_experiment must re-raise the attach error, got "
                      f"{ctx.exception!r}")
        # the driver got past the stale-window check and into phase 1
        self.assertEqual(windows, [_xp._unique_title("ExternPongExp")],
                         "expected exactly one stale-window probe before launch")

        out = _results_path(cfg_path)
        self.assertTrue(out.is_file(), f"no failure results written at {out}")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed",
                         "failure results must be marked status=failed")
        self.assertTrue(data["error"],
                        "failure results must carry a non-empty error string")
        self.assertIn("window", data["error"].lower(),
                      f"error should describe the window/attach problem, got "
                      f"{data['error']!r}")
        self.assertEqual(data["error_type"], "RuntimeError")
        self.assertEqual(data["config_file"], str(cfg_path))

        self.assertEqual(stopped, [proc],
                         "the launched game process must be stopped exactly once "
                         "and not leaked")

    def test_attach_failure_without_results_write_still_raises_original(self):
        """A failing recorder must not mask the run failure (ROOT-027)."""
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch

        stopped = []
        windows = []
        error = RuntimeError("game window 'ExternPongExp-1' did not appear within 20 s")
        cfg_path = self._config_in_tempdir()
        proc = self._stub_pre_attach_phase(error, windows, stopped)
        results_path = _results_path(cfg_path)
        real_open = open

        def spy_open(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and Path(path) == results_path:
                raise OSError("no such directory")
            return real_open(path, *args, **kwargs)

        stderr = io.StringIO()
        with patch("builtins.open", spy_open), redirect_stderr(stderr):
            with self.assertRaises(RuntimeError) as ctx:
                _xp.run_external_experiment(cfg_path)

        self.assertIs(ctx.exception, error,
                      f"recorder failure masked the run error, got {ctx.exception!r}")
        self.assertFalse(results_path.exists(),
                         "results path should not exist when the write failed")
        self.assertIn("could not write failure results", stderr.getvalue(),
                      "recorder must warn on stderr instead of raising")
        self.assertEqual(stopped, [proc],
                         "the game process must still be stopped when the recorder "
                         "fails")

    def test_attach_failure_releases_real_log_handle(self):
        """With the real stop(), an attach failure leaves nothing running open."""
        import json

        windows = []
        error = RuntimeError("game window 'ExternPongExp-1' did not appear within 20 s")
        cfg_path = self._config_in_tempdir()
        proc = self._stub_pre_attach_phase(error, windows, [])
        self._patch(stop=stop)  # real cleanup, not a recorder

        with self.assertRaises(RuntimeError):
            _xp.run_external_experiment(cfg_path)

        self.assertEqual(proc.terminated, 1, "game process was not terminated")
        self.assertEqual(proc._log_closed, 1,  # noqa: SLF001 -- fake handle's log
                         "the launch log handle leaked on the attach-failure path")
        data = json.loads(_results_path(cfg_path).read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed",
                         "failure results must still be written when stop() is real")


if __name__ == "__main__":
    unittest.main()
