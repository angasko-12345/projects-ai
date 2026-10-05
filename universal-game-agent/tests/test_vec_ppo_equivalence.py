"""1-slot vector PPO equivalence: same rollout data as single-env PPO.

A tiny scripted env with a fully known step sequence runs through both
``PPOTrainer(fake)`` and ``PPOTrainer(SyncVectorEnv([factory]))`` under
identical torch seeds. Every rollout artifact is compared exactly, so any
semantic drift in the vector path turns red here. No display, no window.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import torch

    from agent.model import ActorCritic
    from environment.vec import SyncVectorEnv
    from training.ppo import PPOConfig, PPOTrainer, compute_gae

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

OBS_SHAPE = (4, 84, 84)
RESET_VAL = -1.0


class ScriptedEnv:
    """Deterministic fake: scripted (reward, terminated, truncated) per step.

    Reset frames carry RESET_VAL, step frames the 1-based step index, so
    pre-reset, post-reset, and carried observations are distinguishable.
    Resetting restarts the script, like a fresh episode.
    """

    def __init__(self, script):
        self.script = list(script)
        self.t = 0
        self.resets = 0
        self.seen_seeds = []
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, *, seed=None, options=None):
        self.resets += 1
        self.seen_seeds.append(seed)
        self.t = 0
        return np.full(OBS_SHAPE, RESET_VAL, dtype=np.float32), {}

    def step(self, action):
        if self.t < len(self.script):
            reward, terminated, truncated = self.script[self.t]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.t += 1
        obs = np.full(OBS_SHAPE, float(self.t), dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

    def close(self):
        pass


def _config(rollout_length, checkpoint_dir):
    return PPOConfig(rollout_length=rollout_length, minibatch_size=rollout_length,
                     update_epochs=1, total_timesteps=rollout_length,
                     learning_rate=1e-3, checkpoint_dir=checkpoint_dir,
                     checkpoint_every_updates=100)


def _pair(script, rollout_length, seed=0):
    """Two identically-seeded trainers: single-env and 1-slot vector."""
    torch.manual_seed(seed)
    single_env = ScriptedEnv(script)
    single = PPOTrainer(single_env, ActorCritic(num_actions=2),
                        _config(rollout_length, checkpoint_dir="/tmp/never1"))
    torch.manual_seed(seed)
    vec_env = ScriptedEnv(script)
    vec = PPOTrainer(SyncVectorEnv([lambda: vec_env]), ActorCritic(num_actions=2),
                     _config(rollout_length, checkpoint_dir="/tmp/never2"))
    return (single, single_env), (vec, vec_env)


ROLL_SEED = 999


def _rollout(trainer):
    """Collect one rollout from a fixed RNG state, so both paths draw alike."""
    torch.manual_seed(ROLL_SEED)
    return trainer.collect_rollout()


def _assert_same_rollout(test, single_out, vec_out):
    buf1, *rest1 = single_out
    buf2, *rest2 = vec_out
    for key in ("obs", "actions", "logprobs", "values", "ext", "terminated",
                "truncated", "dones", "next_obs", "next_value"):
        test.assertTrue(torch.equal(buf1[key], buf2[key]), key)
    for key in ("rewards", "int_rewards"):
        test.assertTrue(torch.equal(buf1[key], buf2[key]), key)
    for key in ("pixel_change", "int_raw_mean"):
        test.assertEqual(buf1[key], buf2[key], key)
    test.assertEqual(buf1["comp_sums"], buf2["comp_sums"])
    test.assertEqual(rest1, rest2)  # ep_rewards/lengths/ext/int/term lists
    adv1, ret1 = compute_gae(buf1["rewards"].float(), buf1["values"].float().reshape(-1),
                             buf1["terminated"].float(), buf1["next_value"].float(),
                             0.99, 0.95)
    adv2, ret2 = compute_gae(buf2["rewards"].float(), buf2["values"].float().reshape(-1),
                             buf2["terminated"].float(), buf2["next_value"].float(),
                             0.99, 0.95)
    test.assertTrue(torch.equal(adv1, adv2))
    test.assertTrue(torch.equal(ret1, ret2))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestVecPPOEquivalence(unittest.TestCase):
    def test_termination_rollout_matches(self):
        (single, env1), (vec, env2) = _pair(
            [(0.0, False, False), (1.0, True, False), (0.0, False, False)], 6)
        _assert_same_rollout(self, _rollout(single), _rollout(vec))
        self.assertEqual(env1.resets, env2.resets)

    def test_truncation_rollout_matches(self):
        (single, env1), (vec, env2) = _pair(
            [(0.0, False, False), (0.5, False, True), (0.0, False, False)], 6)
        out1, out2 = _rollout(single), _rollout(vec)
        _assert_same_rollout(self, out1, out2)
        # Truncation bootstraps from the final pre-reset frame on both paths.
        self.assertFalse(bool(out1[0]["terminated"][-1]))
        self.assertTrue(bool(out1[0]["truncated"][-1]))
        self.assertEqual(env1.resets, env2.resets)

    def test_multi_episode_rollout_matches(self):
        script = [(1.0, True, False), (0.0, False, False)]  # 2-step episodes
        (single, env1), (vec, vec_env) = _pair(script, 7)
        _assert_same_rollout(self, _rollout(single), _rollout(vec))
        self.assertGreater(env1.resets, 2)
        self.assertEqual(env1.resets, vec_env.resets)

    def test_back_to_back_rollouts_match(self):
        script = [(1.0, True, False), (0.0, False, False), (0.0, False, True)]
        (single, env1), (vec, env2) = _pair(script, 5)
        _assert_same_rollout(self, _rollout(single), _rollout(vec))
        _assert_same_rollout(self, _rollout(single), _rollout(vec))
        self.assertEqual(env1.resets, env2.resets)
        self.assertEqual(single.num_timesteps, vec.num_timesteps)

    def test_update_matches_after_same_rollout(self):
        (single, _), (vec, _) = _pair(
            [(0.0, False, False), (1.0, True, False)], 4)
        buf1, *_ = _rollout(single)
        buf2, *_ = _rollout(vec)
        stats1, stats2 = single.update(buf1), vec.update(buf2)
        self.assertEqual(stats1, stats2)
        for a, b in zip(single.model.parameters(), vec.model.parameters()):
            self.assertTrue(torch.equal(a, b))

    def test_boundary_uses_slot_reset_not_full_reset(self):
        script = [(1.0, True, False), (0.0, False, False)]
        torch.manual_seed(0)
        inner = ScriptedEnv(script)
        vec = SyncVectorEnv([lambda: inner])
        trainer = PPOTrainer(vec, ActorCritic(num_actions=2), _config(6, "/tmp/never3"))
        calls = {"reset": 0, "slot": []}
        orig_reset, orig_slot = vec.reset, vec.reset_env

        def counting_reset(*a, **k):
            calls["reset"] += 1
            return orig_reset(*a, **k)

        def counting_slot(i, *a, **k):
            calls["slot"].append(i)
            return orig_slot(i, *a, **k)

        vec.reset, vec.reset_env = counting_reset, counting_slot
        try:
            trainer.collect_rollout()
        finally:
            vec.reset, vec.reset_env = orig_reset, orig_slot
        self.assertEqual(calls["reset"], 1)  # initial only
        self.assertTrue(len(calls["slot"]) >= 1)
        self.assertTrue(all(i == 0 for i in calls["slot"]))

    def test_multi_slot_vec_rejected(self):
        vec = SyncVectorEnv([lambda: ScriptedEnv([]), lambda: ScriptedEnv([])])
        with self.assertRaises(ValueError):
            PPOTrainer(vec, ActorCritic(num_actions=2), _config(4, "/tmp/never4"))

    def test_short_train_matches_except_fps(self):
        script = [(1.0, True, False), (0.0, False, False), (0.0, False, True)]
        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            env1 = ScriptedEnv(script)
            t1 = PPOTrainer(env1, ActorCritic(num_actions=2),
                            _config(8, str(Path(tmp) / "ckpt1")))
            torch.manual_seed(0)
            env2 = ScriptedEnv(script)
            t2 = PPOTrainer(SyncVectorEnv([lambda: env2]), ActorCritic(num_actions=2),
                            _config(8, str(Path(tmp) / "ckpt2")))
            torch.manual_seed(ROLL_SEED)
            h1 = t1.train()
            torch.manual_seed(ROLL_SEED)
            h2 = t2.train()
            self.assertEqual(env1.resets, env2.resets)
            for key in h1:
                if key == "fps":  # wall-clock by definition
                    continue
                self.assertEqual(h1[key], h2[key], key)


if __name__ == "__main__":
    unittest.main()
