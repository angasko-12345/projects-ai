"""AsyncVectorEnv: the SyncVectorEnv contract, one subprocess per slot (UGA U1).

Every comparison is exact: for the same factories, seeds and actions the
async container must return exactly what the synchronous one returns,
including terminal observations, truncation flags and the explicit
per-slot reset. Workers use the ``spawn`` start method, the only one
Windows has. No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import importlib.util
import unittest

import numpy as np

try:
    import torch  # noqa: F401 - training.experiment imports torch

    from environment.vec import AsyncVectorEnv, AsyncVectorEnvError, SyncVectorEnv
    from training.experiment import ConfigEnvFactory, make_env_for_training, make_env_from_config

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

_HAS_ALE = importlib.util.find_spec("ale_py") is not None

TOY_CFG = {"width": 64, "height": 48, "max_steps": 40, "obs_size": 84, "num_stack": 4, "skip": 1}


class FailingEnv:
    """Module-level (picklable) env whose step raises on demand."""

    def __init__(self, fail_on_step=True):
        self.fail_on_step = fail_on_step

    def reset(self, *, seed=None, options=None):
        return np.zeros((2,), dtype=np.float32), {}

    def step(self, action):
        if self.fail_on_step:
            raise RuntimeError("boom from the worker")
        return np.zeros((2,), dtype=np.float32), 0.0, False, False, {}

    def close(self):
        pass


class ShapeEnv:
    """Picklable env with configurable action count (check_spaces test)."""

    def __init__(self, n):
        from gymnasium import spaces

        self.action_space = spaces.Discrete(n)
        self.observation_space = spaces.Box(0.0, 1.0, (2,), np.float32)

    def reset(self, *, seed=None, options=None):
        return np.zeros((2,), dtype=np.float32), {}

    def step(self, action):
        return np.zeros((2,), dtype=np.float32), 0.0, False, False, {}

    def close(self):
        pass


def _drive(vec, seeds, steps, num_actions, action_seed=123):
    """Fixed action stream; finished slots reset explicitly. Records everything."""
    rng = np.random.default_rng(action_seed)
    trace = []
    obs, infos = vec.reset(seeds=seeds)
    trace.append(("reset", [np.array(o) for o in obs], infos))
    for _ in range(steps):
        actions = [int(a) for a in rng.integers(num_actions, size=vec.num_envs)]
        obs, rew, term, trunc, infos = vec.step(actions)
        trace.append(("step", [np.array(o) for o in obs], list(rew), list(term), list(trunc)))
        for i in range(vec.num_envs):
            if term[i] or trunc[i]:
                o, info = vec.reset_env(i)
                trace.append(("reset_env", i, np.array(o), info))
    return trace


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestAsyncMatchesSync(unittest.TestCase):
    def assertTracesEqual(self, a, b):
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b, strict=True):
            self.assertEqual(x[0], y[0])
            if x[0] == "reset":
                for ox, oy in zip(x[1], y[1], strict=True):
                    self.assertEqual(ox.dtype, oy.dtype)
                    self.assertTrue(np.array_equal(ox, oy))
                self.assertEqual(x[2], y[2])
            elif x[0] == "step":
                for ox, oy in zip(x[1], y[1], strict=True):
                    self.assertEqual(ox.dtype, oy.dtype)
                    self.assertTrue(np.array_equal(ox, oy))
                self.assertEqual(x[2:], y[2:])
                for r in x[2]:
                    self.assertIs(type(r), float)
            else:
                self.assertEqual(x[1], y[1])
                self.assertTrue(np.array_equal(x[2], y[2]))
                self.assertEqual(x[3], y[3])

    def _compare(self, env_cfg, num_envs, steps, seeds):
        factory = ConfigEnvFactory(env_cfg)
        sync = SyncVectorEnv([factory] * num_envs)
        try:
            n_actions = int(sync.single_action_space.n)
            sync_trace = _drive(sync, seeds, steps, n_actions)
        finally:
            sync.close()
        avec = AsyncVectorEnv([factory] * num_envs)
        try:
            self.assertEqual(int(avec.single_action_space.n), n_actions)
            async_trace = _drive(avec, seeds, steps, n_actions)
        finally:
            avec.close()
        self.assertTracesEqual(sync_trace, async_trace)
        return sync_trace

    def test_toy_identical_trajectories_with_terminations_and_truncations(self):
        trace = self._compare(TOY_CFG, 3, 120, seeds=[0, 1, 2])
        self.assertTrue(any(t[0] == "reset_env" for t in trace), "no episode boundary exercised")
        flags = [t for t in trace if t[0] == "step"]
        self.assertTrue(any(any(t[3]) for t in flags), "no termination exercised")
        self.assertTrue(any(any(t[4]) for t in flags), "no truncation exercised")

    def test_toy_uint8_observations_cross_the_pipe_unchanged(self):
        trace = self._compare(dict(TOY_CFG, obs_dtype="uint8"), 2, 30, seeds=[5, 6])
        self.assertEqual(trace[0][1][0].dtype, np.uint8)

    def test_reset_without_seeds_and_reset_env_with_seed(self):
        factory = ConfigEnvFactory(TOY_CFG)
        sync, avec = SyncVectorEnv([factory] * 2), AsyncVectorEnv([factory] * 2)
        try:
            sync.reset(seeds=[3, 4])
            avec.reset(seeds=[3, 4])
            # Unseeded resets continue each env's own RNG, in-process or not.
            a, _ = sync.reset()
            b, _ = avec.reset()
            for x, y in zip(a, b, strict=True):
                self.assertTrue(np.array_equal(x, y))
            self.assertTrue(np.array_equal(sync.reset_env(1, seed=9)[0], avec.reset_env(1, seed=9)[0]))
        finally:
            sync.close()
            avec.close()

    @unittest.skipUnless(_HAS_ALE, "ale-py not installed")
    def test_atari_identical_and_never_autoresets(self):
        cfg = {"type": "atari", "game": "pong", "max_episode_frames": 48, "noop_max": 3,
               "sticky": 0.25, "obs_dtype": "uint8"}
        trace = self._compare(cfg, 2, 30, seeds=[0, 1])
        self.assertTrue(any(t[0] == "reset_env" for t in trace), "no ALE episode boundary exercised")
        # Explicit-reset contract survives the process boundary: stepping a
        # finished ALE slot raises (inside the worker) instead of autoresetting.
        avec = AsyncVectorEnv([ConfigEnvFactory(cfg)])
        try:
            avec.reset(seeds=[0])
            done = False
            for _ in range(100):
                _, _, term, trunc, _ = avec.step([0])
                if term[0] or trunc[0]:
                    done = True
                    break
            self.assertTrue(done)
            with self.assertRaises(AsyncVectorEnvError):
                avec.step([0])
        finally:
            avec.close()


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestAsyncContract(unittest.TestCase):
    def test_worker_exception_is_reraised_with_type_and_message(self):
        avec = AsyncVectorEnv([FailingEnv])
        try:
            avec.reset()
            with self.assertRaises(AsyncVectorEnvError) as ctx:
                avec.step([0])
            self.assertIn("RuntimeError", str(ctx.exception))
            self.assertIn("boom from the worker", str(ctx.exception))
        finally:
            avec.close()

    def test_close_is_idempotent_and_reaps_workers(self):
        avec = AsyncVectorEnv([ConfigEnvFactory(TOY_CFG)] * 2)
        procs = list(avec._procs)
        avec.close()
        avec.close()
        for proc in procs:
            self.assertFalse(proc.is_alive())

    def test_argument_errors_match_sync(self):
        avec = AsyncVectorEnv([ConfigEnvFactory(TOY_CFG)] * 2)
        try:
            with self.assertRaises(ValueError):
                avec.reset(seeds=[1])
            avec.reset(seeds=[1, 2])
            with self.assertRaises(ValueError):
                avec.step([0])
            with self.assertRaises(IndexError):
                avec.reset_env(2)
        finally:
            avec.close()

    def test_check_spaces_rejects_mismatched_slots(self):
        from functools import partial

        with self.assertRaises(ValueError):
            AsyncVectorEnv([partial(ShapeEnv, 3), partial(ShapeEnv, 4)], check_spaces=True)
        avec = AsyncVectorEnv([partial(ShapeEnv, 3), partial(ShapeEnv, 3)], check_spaces=True)
        avec.close()

    def test_is_a_sync_vector_env_for_the_trainer(self):
        avec = AsyncVectorEnv([ConfigEnvFactory(TOY_CFG)])
        try:
            self.assertIsInstance(avec, SyncVectorEnv)
            self.assertEqual(avec.num_envs, 1)
        finally:
            avec.close()


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestBackendWiring(unittest.TestCase):
    def test_async_backend_builds_async_env_only_for_vectors(self):
        make = make_env_from_config(dict(TOY_CFG, vector_backend="async"))
        single = make_env_for_training(make, 1)
        self.assertNotIsInstance(single, SyncVectorEnv)  # num_envs=1: plain env, legacy
        single.close()
        vec = make_env_for_training(make, 2)
        try:
            self.assertIsInstance(vec, AsyncVectorEnv)
        finally:
            vec.close()

    def test_default_backend_is_sync(self):
        vec = make_env_for_training(make_env_from_config(dict(TOY_CFG)), 2)
        try:
            self.assertIs(type(vec), SyncVectorEnv)
        finally:
            vec.close()

    def test_unknown_backend_and_external_async_rejected(self):
        with self.assertRaises(ValueError):
            make_env_from_config(dict(TOY_CFG, vector_backend="threads"))
        with self.assertRaises(ValueError):
            make_env_from_config({"type": "external", "vector_backend": "async"})

    def test_config_factory_is_picklable(self):
        import pickle

        factory = pickle.loads(pickle.dumps(ConfigEnvFactory(dict(TOY_CFG, vector_backend="async"))))
        self.assertEqual(factory.env_cfg["vector_backend"], "sync")
        env = factory()
        env.close()


if __name__ == "__main__":
    unittest.main()
