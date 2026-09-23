"""Tests for the pixel-only toy Pong environment (stdlib unittest)."""
import unittest

import numpy as np

from environment.toy_pong import LEFT, NOOP, RIGHT, ToyPongEnv, _pixel_policy_chase


def _flee(obs):
    """Pixels-only coward policy: run away from the ball (guarantees a miss)."""
    return {NOOP: NOOP, LEFT: RIGHT, RIGHT: LEFT}[_pixel_policy_chase(obs)]


class TestToyPong(unittest.TestCase):
    def test_reset_returns_frame_and_clean_info(self):
        env = ToyPongEnv()
        obs, info = env.reset(seed=0)
        self.assertIsInstance(obs, np.ndarray)
        self.assertEqual(obs.dtype, np.uint8)
        self.assertEqual(info, {})

    def test_observation_shape(self):
        env = ToyPongEnv()
        obs, _ = env.reset(seed=0)
        self.assertEqual(obs.shape, (48, 64, 3))
        self.assertTrue(env.observation_space.contains(obs))
        small = ToyPongEnv(width=32, height=24)
        obs, _ = small.reset(seed=0)
        self.assertEqual(obs.shape, (24, 32, 3))

    def test_action_space(self):
        env = ToyPongEnv()
        self.assertEqual(env.action_space.n, 3)
        env.reset(seed=0)
        for valid in (NOOP, LEFT, RIGHT, np.int64(1)):
            obs, _, _, _, _ = env.step(valid)
            self.assertEqual(obs.shape, (48, 64, 3))

    def test_step_tuple_and_types(self):
        env = ToyPongEnv()
        env.reset(seed=0)
        obs, reward, terminated, truncated, info = env.step(NOOP)
        self.assertIsInstance(obs, np.ndarray)
        self.assertIsInstance(reward, float)
        self.assertIsInstance(terminated, bool)
        self.assertIsInstance(truncated, bool)
        self.assertEqual(info, {})

    def test_action_validation(self):
        env = ToyPongEnv()
        env.reset(seed=0)
        for bad in (-1, 3, 99, None, "1", 1.5, True, [1]):
            with self.assertRaises(ValueError, msg=f"action={bad!r}"):
                env.step(bad)

    def test_reward_values_and_hit(self):
        env = ToyPongEnv()
        obs, _ = env.reset(seed=0)
        rewards = set()
        for _ in range(500):
            obs, reward, terminated, truncated, _ = env.step(_pixel_policy_chase(obs))
            rewards.add(reward)
            if terminated or truncated:
                break
        self.assertTrue(rewards <= {0.0, 1.0, -1.0}, rewards)
        self.assertIn(1.0, rewards)  # chase policy hits the ball

    def test_termination_on_miss(self):
        env = ToyPongEnv()
        obs, _ = env.reset(seed=0)
        total, steps = 0.0, 0
        while True:
            obs, reward, terminated, truncated, _ = env.step(_flee(obs))
            total, steps = total + reward, steps + 1
            if terminated or truncated:
                break
            self.assertLess(steps, 500, "flee policy should miss quickly")
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, -1.0)
        self.assertEqual(total, -1.0)  # only the final miss pays out

    def test_truncation_at_max_steps(self):
        env = ToyPongEnv(max_steps=3)
        env.reset(seed=0)
        for _ in range(2):
            _, _, terminated, truncated, _ = env.step(NOOP)
            self.assertFalse(terminated or truncated)
        _, _, terminated, truncated, _ = env.step(NOOP)
        self.assertFalse(terminated)
        self.assertTrue(truncated)

    def test_deterministic_seeding(self):
        actions = [NOOP, LEFT, RIGHT, LEFT, NOOP] * 6

        def rollout(seed):
            env = ToyPongEnv()
            env.reset(seed=seed)
            frames, rewards = [], []
            for a in actions:
                obs, reward, terminated, truncated, _ = env.step(a)
                frames.append(obs.tobytes())
                rewards.append(reward)
                if terminated or truncated:
                    break
            return frames, rewards

        self.assertEqual(rollout(0), rollout(0))
        frames_a, _ = rollout(0)
        frames_b, _ = rollout(1)
        self.assertNotEqual(frames_a, frames_b)

    def test_no_internal_state_leak(self):
        env = ToyPongEnv()
        obs, info = env.reset(seed=0)
        _, _, _, _, step_info = env.step(NOOP)
        for exposed in (obs, info, step_info):
            blob = repr(exposed)
            self.assertNotIn("velocity", blob)
        self.assertEqual(info, {})
        self.assertEqual(step_info, {})
        banned = ("ball", "paddle", "vel", "score", "collision", "pos", "coord")
        public = [n for n in dir(env) if not n.startswith("_")]
        leaking = [n for n in public if any(b in n.lower() for b in banned)]
        self.assertEqual(leaking, [], f"public state leaks: {leaking}")

    def test_full_episodes_run(self):
        for seed in range(3):
            env = ToyPongEnv()
            obs, _ = env.reset(seed=seed)
            for _ in range(600):
                obs, _, terminated, truncated, _ = env.step(_pixel_policy_chase(obs))
                if terminated or truncated:
                    break
            else:
                self.fail(f"seed {seed}: episode never finished")
            self.assertTrue(terminated or truncated)


if __name__ == "__main__":
    unittest.main()
