"""Tests for environment.vec (stdlib + numpy only, no torch).

All environments are tiny scripted fakes defined below: no window, no
MSS, no SendInput, no game logic.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import unittest

import numpy as np

from environment.vec import SyncVectorEnv


class FakeEnv:
    """Scripted Gymnasium-style env: per-step (reward, terminated, truncated).

    Observations carry ``obs_value`` (reset) or the running step count, so
    tests can tell environments apart. Shapes may differ per env.
    """

    def __init__(self, script=(), obs_value=0.0, shape=(2,), info=None):
        self.script = list(script)
        self.obs_value = obs_value
        self.shape = shape
        self.info = dict(info or {})
        self.step_index = 0
        self.resets = 0
        self.seen_seeds = []
        self.seen_actions = []
        self.closes = 0
        self.action_space = type("Space", (), {"n": 3})()
        self.observation_space = type("Space", (), {"shape": shape})()

    def reset(self, *, seed=None, options=None):
        self.resets += 1
        self.seen_seeds.append(seed)
        self.step_index = 0
        return np.full(self.shape, self.obs_value, dtype=np.float32), dict(self.info)

    def step(self, action):
        self.seen_actions.append(action)
        if self.step_index < len(self.script):
            reward, terminated, truncated = self.script[self.step_index]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.step_index += 1
        obs = np.full(self.shape, float(self.step_index), dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), dict(self.info)

    def close(self):
        self.closes += 1


class ClosingEnv(FakeEnv):
    def __init__(self, *args, fail_on_close=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_on_close = fail_on_close

    def close(self):
        self.closes += 1
        if self.fail_on_close:
            raise RuntimeError("close boom")


class TestSyncVectorEnv(unittest.TestCase):
    def test_one_environment(self):
        vec = SyncVectorEnv([lambda: FakeEnv(obs_value=1.0)])
        try:
            self.assertEqual(vec.num_envs, 1)
            (obs,), (info,) = vec.reset()
            self.assertEqual(tuple(obs.shape), (2,))
            self.assertTrue((obs == 1.0).all())
            out = vec.step([2])
            self.assertEqual(len(out), 5)
            obs_b, rewards, terminateds, truncateds, infos = out
            self.assertEqual(len(obs_b), len(rewards), 1)
            self.assertEqual(rewards, [0.0])
            self.assertEqual(terminateds, [False])
            self.assertEqual(truncateds, [False])
        finally:
            vec.close()

    def test_two_environments(self):
        vec = SyncVectorEnv([lambda: FakeEnv(obs_value=1.0),
                             lambda: FakeEnv(obs_value=2.0)])
        try:
            self.assertEqual(vec.num_envs, 2)
            obs, _ = vec.reset()
            self.assertTrue((obs[0] == 1.0).all())
            self.assertTrue((obs[1] == 2.0).all())
        finally:
            vec.close()

    def test_independent_observations(self):
        made = [FakeEnv(obs_value=5.0, shape=(2,)), FakeEnv(obs_value=7.0, shape=(3,))]
        vec = SyncVectorEnv([lambda: made[0], lambda: made[1]])
        try:
            obs, _ = vec.reset()
            # Native shapes preserved per env: no stacking.
            self.assertEqual(tuple(obs[0].shape), (2,))
            self.assertEqual(tuple(obs[1].shape), (3,))
            stepped, _, _, _, _ = vec.step([0, 0])
            self.assertEqual(tuple(stepped[0].shape), (2,))
            self.assertEqual(tuple(stepped[1].shape), (3,))
        finally:
            vec.close()

    def test_independent_rewards(self):
        vec = SyncVectorEnv([lambda: FakeEnv(script=[(1.0, False, False)]),
                             lambda: FakeEnv(script=[(-1.0, False, False)])])
        try:
            vec.reset()
            _, rewards, _, _, _ = vec.step([0, 0])
            self.assertEqual(rewards, [1.0, -1.0])
        finally:
            vec.close()

    def test_independent_termination(self):
        vec = SyncVectorEnv([lambda: FakeEnv(script=[(0.0, True, False)]),
                             lambda: FakeEnv(script=[(0.0, False, False)])])
        try:
            vec.reset()
            _, _, terminateds, truncateds, _ = vec.step([0, 0])
            self.assertEqual(terminateds, [True, False])
            self.assertEqual(truncateds, [False, False])
        finally:
            vec.close()

    def test_independent_truncation(self):
        vec = SyncVectorEnv([lambda: FakeEnv(script=[(0.0, False, True)]),
                             lambda: FakeEnv(script=[(0.0, False, False)])])
        try:
            vec.reset()
            _, _, terminateds, truncateds, _ = vec.step([0, 0])
            self.assertEqual(terminateds, [False, False])
            self.assertEqual(truncateds, [True, False])
        finally:
            vec.close()

    def test_action_count_validated(self):
        vec = SyncVectorEnv([lambda: FakeEnv(), lambda: FakeEnv()])
        try:
            vec.reset()
            with self.assertRaises(ValueError):
                vec.step([0])
            with self.assertRaises(ValueError):
                vec.step([0, 0, 0])
            with self.assertRaises(ValueError):
                vec.step([])
        finally:
            vec.close()

    def test_reset_resets_all_and_forwards_seeds(self):
        made = [FakeEnv(), FakeEnv()]
        vec = SyncVectorEnv([lambda: made[0], lambda: made[1]])
        try:
            obs, infos = vec.reset(seeds=[11, 22])
            self.assertEqual(len(obs), 2)
            self.assertEqual(len(infos), 2)
            self.assertEqual(made[0].seen_seeds, [11])
            self.assertEqual(made[1].seen_seeds, [22])
            vec.step([0, 0])
            vec.reset()
            self.assertEqual(made[0].resets, 2)
            self.assertEqual(made[1].resets, 2)
            with self.assertRaises(ValueError):
                vec.reset(seeds=[1])
        finally:
            vec.close()

    def test_no_autoreset_after_done(self):
        made = [FakeEnv(script=[(1.0, True, False)], obs_value=9.0)]
        vec = SyncVectorEnv([lambda: made[0]])
        try:
            vec.reset()
            first, _, term, _, _ = vec.step([0])
            self.assertEqual(term, [True])
            second, rewards, _, _, _ = vec.step([1])
            # Pass-through of the env's own post-done step: no hidden reset.
            self.assertEqual(made[0].resets, 1)
            self.assertEqual(made[0].seen_actions, [0, 1])
            self.assertTrue((second[0] == 2.0).all())
            self.assertEqual(rewards, [0.0])
        finally:
            vec.close()

    def test_spaces_come_from_first_env(self):
        vec = SyncVectorEnv([lambda: FakeEnv(), lambda: FakeEnv()])
        try:
            self.assertEqual(vec.single_action_space.n, 3)
            self.assertEqual(tuple(vec.single_observation_space.shape), (2,))
        finally:
            vec.close()

    def test_missing_spaces_read_as_none(self):
        class Bare:
            def reset(self, *, seed=None, options=None):
                return np.zeros(1, dtype=np.float32), {}

            def step(self, action):
                return np.zeros(1, dtype=np.float32), 0.0, False, False, {}

            def close(self):
                pass

        vec = SyncVectorEnv([Bare])
        try:
            self.assertIsNone(vec.single_action_space)
            self.assertIsNone(vec.single_observation_space)
            vec.reset()
            vec.step([0])
        finally:
            vec.close()

    def test_empty_factories_rejected(self):
        with self.assertRaises(ValueError):
            SyncVectorEnv([])

    def test_close_closes_each_exactly_once(self):
        made = [FakeEnv(), FakeEnv(), FakeEnv()]
        vec = SyncVectorEnv([lambda: made[0], lambda: made[1], lambda: made[2]])
        vec.reset()
        vec.close()
        self.assertEqual([e.closes for e in made], [1, 1, 1])

    def test_repeated_close_is_safe(self):
        made = [FakeEnv()]
        vec = SyncVectorEnv([lambda: made[0]])
        vec.close()
        vec.close()
        self.assertEqual(made[0].closes, 1)

    def test_close_failure_still_closes_rest(self):
        made = [ClosingEnv(), ClosingEnv(fail_on_close=True), ClosingEnv()]
        vec = SyncVectorEnv([lambda: made[0], lambda: made[1], lambda: made[2]])
        with self.assertRaises(RuntimeError):
            vec.close()
        self.assertEqual([e.closes for e in made], [1, 1, 1])
        vec.close()  # second call is a no-op, not a second failure
        self.assertEqual([e.closes for e in made], [1, 1, 1])

    def test_construction_failure_cleans_up(self):
        made = []
        boom = RuntimeError("factory boom")

        def ok():
            env = FakeEnv()
            made.append(env)
            return env

        def fail():
            raise boom

        with self.assertRaises(RuntimeError) as ctx:
            SyncVectorEnv([ok, ok, fail])
        self.assertIs(ctx.exception, boom)
        self.assertEqual([e.closes for e in made], [1, 1])


class TestVecEpisodeBoundaries(unittest.TestCase):
    """Boundary semantics decided from ``training/ppo.py:collect_rollout``.

    The rollout needs every terminal step's own observation, reward, and
    flags untouched (truncation bootstrap, curiosity masking, GAE masking),
    resets exactly the finished episode inline, and keeps hidden state
    caller-side. So: no autoreset, separate terminated/truncated flags, and
    selective per-slot reset.
    """

    def test_early_termination_leaves_final_step_intact(self):
        # A terminates on step 2 with reward 1.0; B runs on.
        a = FakeEnv(script=[(0.0, False, False), (1.0, True, False)], obs_value=1.0)
        b = FakeEnv(script=[(0.0, False, False)] * 5, obs_value=2.0)
        vec = SyncVectorEnv([lambda: a, lambda: b])
        try:
            vec.reset()
            vec.step([0, 0])
            obs, rewards, terminateds, truncateds, _ = vec.step([0, 0])
            # A's final step is exact: terminal obs, reward, flags.
            self.assertEqual(terminateds, [True, False])
            self.assertEqual(truncateds, [False, False])
            self.assertEqual(rewards, [1.0, 0.0])
            self.assertTrue((obs[0] == 2.0).all())  # A's own post-step frame
            # No autoreset: the next step still goes to the same envs.
            vec.step([0, 0])
            self.assertEqual(a.resets, 1)
            self.assertEqual(b.resets, 1)
            self.assertEqual(a.seen_actions, [0, 0, 0])
            self.assertEqual(b.step_index, 3)  # B continued independently
        finally:
            vec.close()

    def test_mixed_termination_and_truncation_stay_distinct(self):
        a = FakeEnv(script=[(1.0, True, False)])
        b = FakeEnv(script=[(0.5, False, True)])
        vec = SyncVectorEnv([lambda: a, lambda: b])
        try:
            vec.reset()
            _, rewards, terminateds, truncateds, _ = vec.step([0, 0])
            self.assertEqual(terminateds, [True, False])
            self.assertEqual(truncateds, [False, True])
            self.assertEqual(rewards, [1.0, 0.5])
        finally:
            vec.close()

    def test_truncation_case_no_autoreset(self):
        a = FakeEnv(script=[(0.0, False, False), (0.5, False, True)], obs_value=3.0)
        b = FakeEnv(obs_value=4.0)
        vec = SyncVectorEnv([lambda: a, lambda: b])
        try:
            vec.reset()
            vec.step([0, 0])
            obs, rewards, terminateds, truncateds, _ = vec.step([0, 0])
            self.assertEqual(terminateds, [False, False])
            self.assertEqual(truncateds, [True, False])
            self.assertEqual(rewards, [0.5, 0.0])
            self.assertTrue((obs[0] == 2.0).all())  # pre-reset frame survives
            vec.step([0, 0])
            self.assertEqual(a.resets, 1)  # still no autoreset
        finally:
            vec.close()

    def test_full_reset_resets_both(self):
        a = FakeEnv(script=[(1.0, True, False)])
        b = FakeEnv()
        vec = SyncVectorEnv([lambda: a, lambda: b])
        try:
            vec.reset()
            vec.step([0, 0])
            obs, _ = vec.reset()
            self.assertEqual([e.resets for e in (a, b)], [2, 2])
            self.assertTrue((obs[0] == 0.0).all())
            self.assertTrue((obs[1] == 0.0).all())
        finally:
            vec.close()

    def test_selective_reset_touches_only_finished_slot(self):
        a = FakeEnv(script=[(1.0, True, False)], obs_value=1.0)
        b = FakeEnv(obs_value=2.0)
        vec = SyncVectorEnv([lambda: a, lambda: b])
        try:
            vec.reset()
            vec.step([0, 0])
            vec.step([0, 0])
            obs, _ = vec.reset_env(0)
            self.assertTrue((obs == 1.0).all())  # A fresh
            self.assertEqual(a.resets, 2)
            self.assertEqual(b.resets, 1)  # B untouched, still mid-episode
            self.assertEqual(b.step_index, 2)
            obs, _ = vec.reset_env(1, seed=7)
            self.assertEqual(b.seen_seeds, [None, 7])
            self.assertTrue((obs == 2.0).all())
        finally:
            vec.close()

    def test_selective_reset_rejects_bad_index(self):
        vec = SyncVectorEnv([lambda: FakeEnv()])
        try:
            vec.reset()
            with self.assertRaises(IndexError):
                vec.reset_env(1)
            with self.assertRaises(IndexError):
                vec.reset_env(-1)
        finally:
            vec.close()

    def test_boundary_scenario_still_closes_cleanly(self):
        a = FakeEnv(script=[(1.0, True, False)])
        b = FakeEnv(script=[(0.5, False, True)])
        vec = SyncVectorEnv([lambda: a, lambda: b])
        vec.reset()
        vec.step([0, 0])
        vec.reset_env(0)
        vec.reset_env(1)
        vec.close()
        self.assertEqual([e.closes for e in (a, b)], [1, 1])



if __name__ == "__main__":
    unittest.main()
