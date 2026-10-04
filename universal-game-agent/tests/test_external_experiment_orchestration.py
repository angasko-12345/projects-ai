"""Three-phase orchestration contract for ``training/external_experiment.py``.

Every phase dependency (game process, window manager, env factory, model,
trainer, evaluator) is a deterministic fake, so the whole baseline -> train ->
eval success path runs with no display, no subprocess, and no real Pong
window. Only the driver's own wiring is under test: phase order, what each
phase receives from the previous one, the checkpoint hand-off, and what
happens to a launched game process when a phase fails or the operator
interrupts.

The driver writes checkpoints under a CWD-relative path, so every test here
runs with the CWD pointed at a scratch directory (the standing ROOT-034
DBG-07 rule) and the caller's CWD is restored on teardown.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    import training.external_experiment as _xp
    from interface.window import WindowLostError, WindowNotFoundError

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

# Captured before any test patches the module, so the real cleanup stays
# reachable from the fake that stands in for it. `_xp` only exists when the
# guarded import above succeeded, so these stay None without the dependencies
# instead of raising NameError and turning every skipped test into an error.
_REAL_STOP = getattr(_xp, "stop", None) if _HAS_DEPS else None
_REAL_DUMP = json.dump

_CONFIG_YAML = """\
game:
  title: "OrchExp"
  fps: 60
env:
  type: external
  capture:
    mode: window
    title: "OrchExp"
  lifecycle:
    mode: window
    title: "OrchExp"
model:
  feature_dim: 8
ppo:
  total_timesteps: 16
  rollout_length: 8
  minibatch_size: 8
  update_epochs: 1
  seed: 7
  checkpoint_dir: "checkpoints/orch"
eval:
  episodes: 2
"""

#: seed 7 x 1000 + episode index, for two episodes.
_EVAL_SEEDS = (7000, 7001)
_RUN_TITLE = _xp._unique_title("OrchExp") if _HAS_DEPS else ""
_RUN_CKPT = str(Path("checkpoints/orch") / f"run-{os.getpid()}" / "ppo_final.pt")


def _history(timesteps=16, mean=1.0):
    """A PPO history shaped like the real one (every key the report reads)."""
    return {
        "timesteps": [timesteps],
        "fps": [60.0],
        "policy_loss": [0.1],
        "value_loss": [0.2],
        "entropy": [1.0],
        "mean_reward": [mean],
        "mean_ext_reward": [mean],
        "mean_int_reward": [0.0],
        "predictor_loss": [0.0],
        "pixel_change": [1.0],
        "episodes": [1],
        "upd_episodes": [1],
        "upd_mean_length": [10.0],
        "upd_terminated": [1],
        "upd_truncated": [0],
        "upd_mean_ext": [mean],
        "upd_mean_int": [0.0],
        "upd_mean_total": [mean],
        "components": [{"hit": 0.1}],
    }


def _eval_report(mean, semantics="sign"):
    """An ``evaluate()`` report carrying everything ``summarize_eval`` reads."""
    report = {
        "episodes": 2,
        "greedy": True,
        "seeds": list(_EVAL_SEEDS),
        "reward_semantics": semantics,
        "mean_reward": mean,
        "std_reward": 1.0,
        "min_reward": mean,
        "max_reward": mean,
        "mean_length": 10.0,
        "episode_rewards": [mean, mean],
        "episode_lengths": [10, 10],
        "action_counts": {0: 5, 1: 5},
        "episode_positive_reward_steps": [1, 1],
        "episode_negative_reward_steps": [0, 0],
        "mean_positive_reward_steps": 1.0,
        "mean_negative_reward_steps": 0.0,
        "episode_terminated": [True, False],
        "episode_truncated": [False, True],
        "terminated_episodes": 1,
        "truncated_episodes": 1,
    }
    if semantics == "sign":
        report["episode_hits"] = [1, 1]
        report["episode_misses"] = [0, 0]
        report["mean_hits"] = 1.0
        report["mean_misses"] = 0.0
    return report


class _FakeEnv:
    """Env stand-in: only the probe, close accounting, and identity matter."""

    def __init__(self, index):
        self.index = index
        self.action_space = type("_Space", (), {"n": 3})()
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeModel:
    def __init__(self, kwargs):
        self.kwargs = dict(kwargs)


class _FakeProc:
    """Popen stand-in: records terminate/kill/wait and releases a log handle."""

    def __init__(self, phase):
        self.phase = phase
        self.terminated = 0
        self.killed = 0
        self.returncode = None
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
        return self.returncode


@unittest.skipUnless(_HAS_DEPS, "torch not installed")
class _OrchestrationCase(unittest.TestCase):
    """Stubs every phase dependency and records what the driver actually did."""

    def setUp(self):
        self.events = []
        self.procs = []
        self.envs = []
        self.saved = {}
        self.train_script = []
        self.eval_reports = []
        self.make_env = None
        scratch = tempfile.TemporaryDirectory(prefix="uga-orch-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        previous = os.getcwd()
        os.chdir(self.scratch)
        self.addCleanup(os.chdir, previous)
        self.config_path = self.scratch / "exp.yaml"
        self.config_path.write_text(_CONFIG_YAML, encoding="utf-8")

    # -- stubbing --------------------------------------------------------
    def _patch(self, **kw):
        old = {k: getattr(_xp, k) for k in kw}
        for k, v in kw.items():
            setattr(_xp, k, v)
        self.addCleanup(lambda: [setattr(_xp, k, v) for k, v in old.items()])

    def _fake_trainer_class(self):
        """PPOTrainer stand-in: real checkpoint files, no torch.

        ``load_checkpoint`` restores the model object and step count recorded by
        ``save_checkpoint`` and takes its checkpoint dir from the checkpoint
        path, exactly like the real loader reads it from the payload.
        """
        events, saved, script = self.events, self.saved, self.train_script

        class _FakeTrainer:
            def __init__(self, env, model, config, env_config=None):
                self.env, self.model, self.config = env, model, config
                self.env_config = env_config
                self.num_timesteps = 0
                self.num_updates = 0
                events.append(("trainer_built", env, model, config, env_config))

            def train(self):
                events.append(("train", None))
                item = script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                self.num_timesteps = item["timesteps"]
                self.num_updates = len(item["history"].get("timesteps", []))
                if not item.get("skip_final_write"):
                    self.save_checkpoint(Path(self.config.checkpoint_dir) / "ppo_final.pt")
                return item["history"]

            def save_checkpoint(self, path):
                path = str(path)
                events.append(("save_checkpoint", path))
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(json.dumps({"num_timesteps": self.num_timesteps}),
                                      encoding="utf-8")
                saved[path] = (self.model, self.num_timesteps)
                return path

            @classmethod
            def load_checkpoint(cls, path, env, device="cpu"):
                path = str(path)
                if path not in saved:
                    raise FileNotFoundError(path)  # torch.load on a missing file
                model, steps = saved[path]
                config = type("_Cfg", (), {"checkpoint_dir": str(Path(path).parent)})()
                trainer = cls(env, model, config)
                trainer.num_timesteps = steps
                events.append(("load_checkpoint", path, env, trainer))
                return trainer

        return _FakeTrainer

    def _stub_phases(self):
        """Replace every dependency the driver touches and start recording.

        ``stop`` records and then runs the real cleanup, so ``terminated`` and
        the log-handle count prove the driver's own teardown path ran.
        """
        events = self.events

        def make_external_env_from_config(env_cfg):
            events.append(("env_factory", env_cfg))

            def factory():
                env = _FakeEnv(len(self.envs))
                self.envs.append(env)
                events.append(("env", env))
                return env

            self.make_env = factory
            return factory

        def window_manager(title):
            def attach():
                events.append(("stale_probe", title))
                raise WindowNotFoundError(f"no window titled {title!r}")

            return type("_WM", (), {"attach": staticmethod(attach)})()

        def launch_game(title, seed, game_fps, phase, geometry=None):
            proc = _FakeProc(phase)
            self.procs.append(proc)
            events.append(("launch", phase))
            return proc

        def wait_attach(title, timeout_s=20.0):
            events.append(("attach", title))

        def check_alive(proc, phase):
            events.append(("alive", phase))

        def stop(proc):
            events.append(("stop", proc.phase))
            _REAL_STOP(proc)

        def evaluate(model, make_env, episodes=20, seeds=None, greedy=True):
            events.append(("eval", model, make_env, episodes, tuple(seeds or ())))
            report = self.eval_reports.pop(0)
            if isinstance(report, BaseException):
                raise report
            return report

        def actor_critic(**kwargs):
            model = _FakeModel(kwargs)
            events.append(("model", model))
            return model

        self._patch(make_external_env_from_config=make_external_env_from_config,
                    WindowManager=window_manager,
                    launch_game=launch_game,
                    wait_attach=wait_attach,
                    check_alive=check_alive,
                    stop=stop,
                    evaluate=evaluate,
                    ActorCritic=actor_critic,
                    PPOTrainer=self._fake_trainer_class())

    # -- driving ---------------------------------------------------------
    def _spy_dump(self, obj, fp, **kw):
        self.events.append(("results_written", None))
        return _REAL_DUMP(obj, fp, **kw)

    def _run(self):
        with redirect_stdout(io.StringIO()), patch("json.dump", self._spy_dump):
            return _xp.run_external_experiment(self.config_path)

    def _results(self):
        return json.loads(_xp._results_path(self.config_path).read_text(encoding="utf-8"))

    def _trace(self):
        """Ordered, comparable view of everything the driver did."""
        labels = {
            "env_factory": "env_factory",
            "trainer_built": "trainer_built",
            "results_written": "results_written",
        }
        out = []
        for event in self.events:
            kind = event[0]
            if kind in labels:
                out.append(labels[kind])
            elif kind in ("stale_probe", "attach", "env", "model", "train"):
                out.append(kind)
            elif kind == "launch":
                out.append(f"launch:{event[1]}")
            elif kind == "stop":
                out.append(f"stop:{event[1]}")
            elif kind == "alive":
                out.append(f"alive:{event[1]}")
            elif kind == "eval":
                out.append(f"eval:episodes={event[3]}")
            elif kind in ("save_checkpoint", "load_checkpoint"):
                out.append(f"{'save' if kind[0] == 's' else 'load'}:{Path(event[1]).name}")
            else:  # pragma: no cover - a new event kind means a new assertion
                out.append(kind)
        return out


class TestPhaseOrder(_OrchestrationCase):
    """Baseline eval -> PPO training -> trained eval, once each, in that order."""

    def setUp(self):
        super().setUp()
        self._stub_phases()
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self.eval_reports.extend([_eval_report(1.0), _eval_report(2.0)])

    def test_all_three_phases_run_once_each_in_order(self):
        self._run()
        self.assertEqual(self._trace(), [
            "env_factory",
            "env",              # probe env, only to read action_space.n
            "stale_probe",      # refuse to share a window with a stale game
            "launch:phase1",
            "attach",
            "model",            # fresh untrained model for the baseline
            "eval:episodes=2",
            "alive:phase 1 baseline eval",
            "stop:phase1",
            "env",              # training env
            "model",            # fresh untrained model for training
            "trainer_built",
            "launch:phase2",
            "attach",
            "alive:phase 2 startup",
            "train",
            "save:ppo_final.pt",
            "stop:phase2",
            "launch:phase3",
            "attach",
            "env",              # throwaway env for loading the checkpoint
            "trainer_built",
            "load:ppo_final.pt",
            "eval:episodes=2",
            "alive:phase 3 final eval",
            "stop:phase3",
            "results_written",
        ], "every phase must run exactly once, in order, each torn down before "
            "the next one starts")

    def test_no_phase_is_silently_skipped(self):
        self._run()
        launches = [e[1] for e in self.events if e[0] == "launch"]
        evals = [e for e in self.events if e[0] == "eval"]
        self.assertEqual(launches, ["phase1", "phase2", "phase3"])
        self.assertEqual(len(evals), 2, "baseline and final eval must both run")
        self.assertEqual(len([e for e in self.events if e[0] == "train"]), 1)
        self.assertEqual(len([e for e in self.events if e[0] == "load_checkpoint"]), 1)

    def test_each_phase_is_torn_down_before_the_next_one_starts(self):
        self._run()
        trace = self._trace()
        for phase in ("phase1", "phase2", "phase3"):
            self.assertEqual(trace.count(f"launch:{phase}"), 1)
            self.assertLess(trace.index(f"launch:{phase}"), trace.index(f"stop:{phase}"),
                            f"{phase} was not stopped after it started")
            self.assertEqual(trace.count(f"stop:{phase}"), 1,
                             f"the {phase} game process must be stopped exactly once")
        self.assertEqual([p.terminated for p in self.procs], [1, 1, 1])

    def test_both_evals_use_the_same_episode_budget_and_seeds(self):
        self._run()
        evals = [e for e in self.events if e[0] == "eval"]
        self.assertEqual([e[3] for e in evals], [2, 2])
        self.assertEqual([e[4] for e in evals], [_EVAL_SEEDS, _EVAL_SEEDS],
                         "the untrained and trained evals must be comparable")

    def test_every_phase_uses_the_same_env_factory(self):
        self._run()
        factories = [e for e in self.events if e[0] == "env_factory"]
        self.assertEqual(len(factories), 1, "one env factory, built once")
        env_cfgs = [e[2] for e in self.events if e[0] == "eval"]
        self.assertTrue(env_cfgs)
        for make_env in env_cfgs:
            self.assertIs(make_env, self.make_env,
                          "a phase built its own envs instead of using the "
                          "factory the driver built")

    def test_probe_env_is_closed_and_its_action_count_reaches_both_models(self):
        self._run()
        self.assertEqual(self.envs[0].closed, 1, "the probe env leaked")
        models = [e[1] for e in self.events if e[0] == "model"]
        self.assertEqual([m.kwargs for m in models],
                         [{"num_actions": 3, "feature_dim": 8},
                          {"num_actions": 3, "feature_dim": 8}],
                         "model config from the file, sized from the probed env")
        self.assertIsNot(models[0], models[1],
                         "the trained phase must not reuse the baseline model")


class TestPhasePropagation(_OrchestrationCase):
    """What each phase must receive from the phase before it."""

    def setUp(self):
        super().setUp()
        self._stub_phases()
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self.eval_reports.extend([_eval_report(1.0), _eval_report(2.0)])
        self.report = self._run()

    def test_trainer_gets_the_run_isolated_config_and_retargeted_env(self):
        built = [e for e in self.events if e[0] == "trainer_built"][0]
        _kind, env, model, config, env_config = built
        self.assertIs(model, [e[1] for e in self.events if e[0] == "model"][1])
        self.assertEqual(config.checkpoint_dir,
                         str(Path("checkpoints/orch") / f"run-{os.getpid()}"),
                         "phase 2 must train into this run's own directory")
        self.assertEqual(env_config["capture"]["title"], _RUN_TITLE)
        self.assertEqual(env_config["lifecycle"]["title"], _RUN_TITLE)
        self.assertEqual(env_config["type"], "external")

    def test_env_config_is_retargeted_before_any_env_is_built(self):
        factory_cfg = [e[1] for e in self.events if e[0] == "env_factory"][0]
        self.assertEqual(factory_cfg["capture"]["title"], _RUN_TITLE)
        self.assertEqual(factory_cfg["lifecycle"]["title"], _RUN_TITLE)

    def test_phase3_evaluates_the_checkpoint_phase2_wrote(self):
        loaded = [e for e in self.events if e[0] == "load_checkpoint"][0]
        path = Path(loaded[1])
        self.assertEqual(str(path), _RUN_CKPT)
        self.assertTrue(path.is_file(), "phase 3 loaded a checkpoint that was never written")
        self.assertEqual(self.report["checkpoint"], str(path))
        evals = [e for e in self.events if e[0] == "eval"]
        self.assertIs(loaded[3].model, evals[1][1],
                      "the final eval must run the checkpointed weights")
        self.assertIsNot(loaded[3].model, evals[0][1],
                         "the final eval must not run the untrained model")

    def test_checkpoint_never_lands_in_the_shared_config_directory(self):
        self.assertTrue(Path(_RUN_CKPT).is_file())
        self.assertFalse((Path("checkpoints/orch") / "ppo_final.pt").exists(),
                         "a run must not overwrite the shared config checkpoint dir")
        self.assertEqual(self.report["ppo_config"]["checkpoint_dir"],
                         "checkpoints/orch", "the report echoes the config file verbatim")


class TestFinalization(_OrchestrationCase):
    """The success artifact is written last and does not claim a failure."""

    def setUp(self):
        super().setUp()
        self._stub_phases()
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self.eval_reports.extend([_eval_report(1.0), _eval_report(2.0)])
        self.report = self._run()

    def test_results_are_written_after_the_last_phase_is_torn_down(self):
        self.assertEqual(self.events[-1][0], "results_written",
                         "finalization must be the last thing the run does")
        self.assertEqual(self._trace()[-2], "stop:phase3")

    def test_results_carry_the_run_identity_and_training_outcome(self):
        data = self._results()
        self.assertEqual(data["seed"], 7)
        self.assertEqual(data["game"]["title"], _RUN_TITLE)
        self.assertEqual(data["checkpoint"], _RUN_CKPT)
        self.assertEqual(data["training_steps"], 16)
        self.assertEqual(data["window_relaunches"], 0)
        self.assertGreaterEqual(data["training_seconds"], 0.0)
        self.assertGreater(data["decision_fps"], 0.0)
        self.assertEqual(data["final_train_rolling_mean_reward"], 1.0)
        self.assertEqual(data["train_terminated_episodes"], 1)
        self.assertEqual(data["train_truncated_episodes"], 0)
        self.assertEqual(data["history_tail"]["timesteps"], [16])

    def test_results_compare_the_untrained_and_trained_runs(self):
        comparison = self._results()["comparison"]
        self.assertEqual(comparison["untrained"]["mean_reward"], 1.0)
        self.assertEqual(comparison["trained"]["mean_reward"], 2.0)
        difference = comparison["difference_trained_minus_untrained"]
        self.assertEqual(difference["mean_reward"], 1.0)
        self.assertEqual(difference["reward_semantics"], "sign")
        self.assertEqual(difference["mean_hits"], 0.0,
                         "hit metrics follow the declared reward semantics")

    def test_a_completed_run_is_not_marked_failed(self):
        self.assertNotEqual(self._results().get("status"), "failed")

    def test_every_game_process_was_stopped_and_its_log_released(self):
        for proc in self.procs:
            self.assertEqual(proc.terminated, 1, f"{proc.phase} process leaked")
            self.assertEqual(proc._log_closed, 1,  # noqa: SLF001 -- fake handle
                             f"{proc.phase} launch log leaked")


class TestPhaseFailurePropagation(_OrchestrationCase):
    """A phase that fails stops the run there, with a failed-status artifact."""

    def _assert_failed_artifact(self, error):
        data = self._results()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["error_type"], type(error).__name__)
        self.assertIn(str(error), data["error"])
        self.assertEqual(data["config_file"], str(self.config_path))
        self.assertNotIn("final_eval", data, "a failed run must not publish an eval")
        self.assertNotIn("comparison", data, "a failed run must not publish a verdict")

    def test_phase1_failure_stops_the_game_and_skips_the_other_phases(self):
        self._stub_phases()
        error = RuntimeError("phase 1 eval died")
        self.eval_reports.append(error)
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIs(ctx.exception, error)
        self.assertEqual([p.phase for p in self.procs], ["phase1"])
        self.assertEqual(self.procs[0].terminated, 1)
        self._assert_failed_artifact(error)

    def test_phase2_failure_stops_the_game_and_never_reaches_phase3(self):
        self._stub_phases()
        error = RuntimeError("torch exploded mid-rollout")
        self.eval_reports.append(_eval_report(1.0))
        self.train_script.append(error)
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIs(ctx.exception, error)
        self.assertEqual([p.phase for p in self.procs], ["phase1", "phase2"])
        phase2 = self.procs[1]
        self.assertEqual(phase2.terminated, 1, "the phase-2 game process leaked")
        self.assertEqual(phase2._log_closed, 1,  # noqa: SLF001 -- fake handle
                         "the phase-2 launch log leaked")
        self._assert_failed_artifact(error)

    def test_phase2_failure_closes_the_training_env(self):
        self._stub_phases()
        self.eval_reports.append(_eval_report(1.0))
        self.train_script.append(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self._run()
        training_env = [e[1] for e in self.events if e[0] == "trainer_built"][0]
        self.assertEqual(training_env.closed, 1, "the training env leaked")

    def test_phase3_failure_stops_the_game_and_marks_the_run_failed(self):
        self._stub_phases()
        error = RuntimeError("final eval died")
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self.eval_reports.extend([_eval_report(1.0), error])
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIs(ctx.exception, error)
        self.assertEqual([p.phase for p in self.procs], ["phase1", "phase2", "phase3"])
        self.assertEqual(self.procs[2].terminated, 1)
        self._assert_failed_artifact(error)

    def test_missing_final_checkpoint_fails_the_run_instead_of_scoring_it(self):
        self._stub_phases()
        self.train_script.append({"timesteps": 16, "history": _history(16),
                                  "skip_final_write": True})
        self.eval_reports.append(_eval_report(1.0))
        with self.assertRaises(FileNotFoundError) as ctx:
            self._run()
        self.assertEqual(self.procs[2].terminated, 1)
        self._assert_failed_artifact(ctx.exception)


class TestCancellation(_OrchestrationCase):
    """An interrupted run must not report a completed experiment."""

    def test_cancellation_during_phase2_stops_the_game_process(self):
        self._stub_phases()
        self.eval_reports.append(_eval_report(1.0))
        self.train_script.append(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self._run()
        self.assertEqual([p.phase for p in self.procs], ["phase1", "phase2"])
        phase2 = self.procs[1]
        self.assertEqual(phase2.terminated, 1,
                         "an interrupted run must not leave the game window up")
        self.assertEqual(phase2._log_closed, 1)  # noqa: SLF001 -- fake handle

    def test_cancellation_closes_the_training_env(self):
        self._stub_phases()
        self.eval_reports.append(_eval_report(1.0))
        self.train_script.append(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self._run()
        training_env = [e[1] for e in self.events if e[0] == "trainer_built"][0]
        self.assertEqual(training_env.closed, 1, "an interrupt leaked the training env")

    def test_cancellation_writes_no_results_artifact(self):
        self._stub_phases()
        self.eval_reports.append(_eval_report(1.0))
        self.train_script.append(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self._run()
        self.assertFalse(
            [e for e in self.events if e[0] == "results_written"],
            "an interrupted run is not a run outcome: recording one would let a "
            "cancelled experiment read as a completed one")

    def test_cancellation_during_phase1_stops_the_game(self):
        self._stub_phases()
        self.eval_reports.append(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self._run()
        self.assertEqual([p.phase for p in self.procs], ["phase1"])
        self.assertEqual(self.procs[0].terminated, 1)
        self.assertFalse([e for e in self.events if e[0] == "results_written"])

    def test_cancellation_during_phase3_stops_the_game(self):
        self._stub_phases()
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self.eval_reports.extend([_eval_report(1.0), KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self._run()
        self.assertEqual(self.procs[2].terminated, 1)
        self.assertFalse([e for e in self.events if e[0] == "results_written"])


class TestWindowLossRelaunch(_OrchestrationCase):
    """Phase 2 tolerates a dead game window and keeps one run checkpoint dir."""

    def setUp(self):
        super().setUp()
        self._stub_phases()
        self.eval_reports.extend([_eval_report(1.0), _eval_report(2.0)])

    def test_window_loss_relaunches_the_game_and_reuses_the_run_checkpoint_dir(self):
        self.train_script.append(WindowLostError("target window is gone"))
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        report = self._run()

        self.assertEqual([p.phase for p in self.procs],
                         ["phase1", "phase2", "phase2", "phase3"])
        self.assertEqual([p.terminated for p in self.procs], [1, 1, 1, 1])
        trace = self._trace()
        self.assertLess(trace.index("stop:phase2"), trace.index("launch:phase3"))
        self.assertLess(trace.index("save:ppo_interrupted.pt"),
                        len(trace) - 1 - trace[::-1].index("launch:phase2"),
                        "the interrupted state must be persisted before relaunching")

        run_dir = Path("checkpoints/orch") / f"run-{os.getpid()}"
        self.assertTrue((run_dir / "ppo_interrupted.pt").is_file())
        self.assertTrue((run_dir / "ppo_final.pt").is_file())
        self.assertEqual(report["window_relaunches"], 1)
        self.assertEqual(report["checkpoint"], str(run_dir / "ppo_final.pt"))
        self.assertEqual(report["training_steps"], 16)

    def test_resumed_attempt_keeps_the_trained_state_and_the_run_directory(self):
        self.train_script.append(WindowLostError("target window is gone"))
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        self._run()

        resumes = [e for e in self.events if e[0] == "load_checkpoint"]
        self.assertEqual(len(resumes), 2, "one resume load, one final-eval load")
        resumed = resumes[0][3]
        self.assertEqual(resumed.config.checkpoint_dir,
                         str(Path("checkpoints/orch") / f"run-{os.getpid()}"),
                         "the resumed trainer must keep writing to the run dir")
        first_model = [e for e in self.events if e[0] == "trainer_built"][0][2]
        self.assertIs(resumed.model, first_model,
                      "a relaunch must not restart from a fresh model")

    def test_giving_up_after_the_relaunch_budget_stops_the_game_and_fails(self):
        error = WindowLostError("gone")
        self.train_script.extend([error] * 4)
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn("giving up", str(ctx.exception))
        self.assertEqual([p.phase for p in self.procs],
                         ["phase1", "phase2", "phase2", "phase2", "phase2"])
        self.assertTrue(all(p.terminated == 1 for p in self.procs))
        self.assertEqual(self._results()["status"], "failed")

    def test_no_op_resume_reports_zero_updates_instead_of_crashing(self):
        """ROOT-014: a resumed checkpoint already at the budget trains nothing.

        ``train()`` then returns an empty history by contract, so the report
        has to aggregate it rather than index into it -- and "nothing was
        trained" has to be visible in the artifact, not read as a zero reward.
        """
        self.train_script.append(WindowLostError("window died at the budget"))
        self.train_script.append({"timesteps": 16, "history": {}})
        report = self._run()

        self.assertEqual(report["window_relaunches"], 1)
        self.assertEqual(report["training_steps"], 16)
        self.assertEqual(report["training_updates"], 0)
        self.assertEqual(report["final_train_rolling_mean_reward"], 0.0)
        self.assertEqual(report["final_train_rolling_mean_ext_reward"], 0.0)
        self.assertEqual(report["train_mean_episode_length"], 0.0)
        self.assertEqual(report["train_terminated_episodes"], 0)
        self.assertEqual(report["train_truncated_episodes"], 0)
        self.assertEqual(report["train_component_means"], {})
        self.assertEqual(self._results().get("status"), None)

    def test_session_lost_at_an_episode_boundary_is_a_relaunchable_window_loss(self):
        """The env reports an absent session from reset(), not from capture().

        Only a capture-time window loss used to count, so a game that died
        between steps ended the whole experiment instead of relaunching.
        """
        from environment.external_game import SessionUnavailableError

        self.train_script.append(SessionUnavailableError(
            "external game session unavailable and attach() failed"))
        self.train_script.append({"timesteps": 16, "history": _history(16)})
        report = self._run()

        self.assertEqual([p.phase for p in self.procs],
                         ["phase1", "phase2", "phase2", "phase3"])
        self.assertEqual(report["window_relaunches"], 1)
        self.assertEqual(report["training_steps"], 16)
        self.assertTrue(all(p.terminated == 1 for p in self.procs))


if __name__ == "__main__":
    unittest.main()