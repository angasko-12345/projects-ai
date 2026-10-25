"""Arcade games: common contract, seeding, variation, registry, vector guard.

Pure numpy; no window, no input backend, no torch.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import unittest

import numpy as np

from environment.arcade import (
    ACTION_MEANINGS,
    FAR_HELDOUT_GAME,
    GAMES,
    HELDOUT_GAME,
    TRAIN_GAMES,
    VariedPongEnv,
    check_game_env,
    make_game,
    make_game_factory,
)
from environment.toy_pong import ToyPongEnv
from environment.vec import SyncVectorEnv
from training.arcade_baselines import POLICIES, run_policy


def _rollout(env, seed, actions):
    frames = [env.reset(seed=seed)[0]]
    rewards = []
    for a in actions:
        f, r, term, trunc, _ = env.step(a)
        frames.append(f)
        rewards.append(r)
        if term or trunc:
            break
    return frames, rewards


class TestContract(unittest.TestCase):
    def test_every_game_meets_the_contract(self):
        for name in GAMES:
            with self.subTest(game=name):
                check_game_env(make_game(name), seed=7, steps=700)

    def test_toy_pong_meets_the_contract_too(self):
        # The contract is the one ToyPongEnv already followed; it still passes.
        check_game_env(ToyPongEnv(), seed=0, steps=300)

    def test_shared_action_set(self):
        self.assertEqual(ACTION_MEANINGS, ("NOOP", "LEFT", "RIGHT"))
        for name in GAMES:
            env = make_game(name)
            self.assertEqual(env.action_space.n, 3)
            self.assertEqual(tuple(env.action_meanings), ACTION_MEANINGS)

    def test_invalid_actions_rejected(self):
        for name in GAMES:
            env = make_game(name)
            env.reset(seed=0)
            for bad in (3, -1, True, 1.0, "1"):
                with self.subTest(game=name, action=bad), self.assertRaises(ValueError):
                    env.step(bad)

    def test_truncation_at_max_steps_not_termination(self):
        # A served volley ball needs > 15 steps to fall to the ground, so the
        # 15-step cap must end the episode as a truncation, not a termination.
        for seed in range(5):
            env = make_game("volley", max_steps=15)
            env.reset(seed=seed)
            for step in range(15):
                _, _, term, trunc, _ = env.step(0)
                self.assertFalse(term)
                self.assertEqual(trunc, step == 14)

    def test_unknown_game_rejected(self):
        with self.assertRaises(ValueError):
            make_game("tetris")
        with self.assertRaises(ValueError):
            make_game_factory("tetris")


class TestSeedingAndVariation(unittest.TestCase):
    def test_same_seed_same_episode(self):
        actions = list(np.random.default_rng(0).integers(0, 3, size=200))
        for name in GAMES:
            with self.subTest(game=name):
                f1, r1 = _rollout(make_game(name), 11, actions)
                f2, r2 = _rollout(make_game(name), 11, actions)
                self.assertEqual(r1, r2)
                self.assertEqual(len(f1), len(f2))
                for a, b in zip(f1, f2, strict=True):
                    np.testing.assert_array_equal(a, b)

    def test_different_seeds_vary_the_episode(self):
        for name in GAMES:
            with self.subTest(game=name):
                firsts = [make_game(name).reset(seed=s)[0] for s in range(6)]
                distinct = {f.tobytes() for f in firsts}
                self.assertGreaterEqual(len(distinct), 5, "per-seed variation should change the frame")

    def test_unseeded_reset_continues_the_rng(self):
        env = make_game("catch")
        a = env.reset(seed=1)[0]
        b = env.reset()[0]
        self.assertFalse(np.array_equal(a, b))
        env2 = make_game("catch")
        env2.reset(seed=1)
        np.testing.assert_array_equal(b, env2.reset()[0])

    def test_varied_pong_keeps_toy_pong_rules(self):
        env = VariedPongEnv()
        self.assertEqual(env.reward_semantics, ToyPongEnv.reward_semantics)
        env.reset(seed=0)
        rewards = []
        for _ in range(500):
            _, r, term, trunc, _ = env.step(0)
            rewards.append(r)
            if term or trunc:
                break
        self.assertTrue(set(rewards) <= {-1.0, 0.0, 1.0})

    def test_toy_pong_default_unchanged(self):
        # Legacy toy Pong must stay white-on-black 64x48 with fixed sizes.
        env = ToyPongEnv()
        frame, _ = env.reset(seed=0)
        self.assertEqual(frame.shape, (48, 64, 3))
        self.assertEqual(set(np.unique(frame)), {0, 255})
        self.assertEqual((env._paddle_w, env._paddle_speed, env._ball_speed), (12, 3, 2))


class TestRegistryAndSplit(unittest.TestCase):
    def test_heldout_games_are_not_training_games(self):
        self.assertEqual(TRAIN_GAMES, ("pong", "catch", "breakout"))
        self.assertNotIn(HELDOUT_GAME, TRAIN_GAMES)
        self.assertNotIn(FAR_HELDOUT_GAME, TRAIN_GAMES)
        self.assertEqual(set(GAMES), {*TRAIN_GAMES, HELDOUT_GAME, FAR_HELDOUT_GAME})

    def test_factory_yields_standard_pixel_obs(self):
        for name in GAMES:
            env = make_game_factory(name, obs_size=48)()
            obs, info = env.reset(seed=0)
            self.assertEqual(obs.shape, (4, 48, 48))
            self.assertEqual(obs.dtype, np.float32)
            self.assertEqual(info, {})


class TestVectorGuard(unittest.TestCase):
    def test_mixed_games_accepted_when_spaces_agree(self):
        vec = SyncVectorEnv([make_game_factory(g, obs_size=48) for g in TRAIN_GAMES], check_spaces=True)
        obs, _ = vec.reset(seeds=[0, 1, 2])
        self.assertEqual([o.shape for o in obs], [(4, 48, 48)] * 3)
        vec.close()

    def test_mismatched_obs_shape_rejected_and_closed(self):
        closed = []

        def make(size):
            def f():
                env = make_game_factory("catch", obs_size=size)()
                orig = env.close

                def close():
                    closed.append(size)
                    orig()
                env.close = close
                return env
            return f

        with self.assertRaises(ValueError):
            SyncVectorEnv([make(48), make(42)], check_spaces=True)
        self.assertEqual(sorted(closed), [42, 48])

    def test_default_does_not_check(self):
        # Legacy contract: unverified unless asked.
        vec = SyncVectorEnv([make_game_factory("catch", obs_size=48),
                             make_game_factory("catch", obs_size=42)])
        self.assertEqual(vec.num_envs, 2)
        vec.close()


class TestBaselines(unittest.TestCase):
    def test_policies_run_and_oracle_beats_random(self):
        seeds = [100, 101, 102, 103]
        for game in GAMES:
            with self.subTest(game=game):
                res = {p: run_policy(game, p, seeds, max_steps=300) for p in POLICIES}
                self.assertGreater(res["oracle"]["mean_reward"], res["random"]["mean_reward"])
                for rep in res.values():
                    self.assertEqual(rep["episodes"], 4)

    def test_skip_repeats_each_decision(self):
        seeds = [100, 101, 102]
        for game in GAMES:
            with self.subTest(game=game):
                one = run_policy(game, "const_left", seeds, max_steps=300, skip=1)
                four = run_policy(game, "const_left", seeds, max_steps=300, skip=4)
                # A constant policy plays the same frames; only decisions are counted.
                self.assertEqual(one["episode_rewards"], four["episode_rewards"])
                for a, b in zip(one["episode_lengths"], four["episode_lengths"], strict=True):
                    self.assertEqual(b, -(-a // 4))
                ora = run_policy(game, "oracle", seeds, max_steps=300, skip=4)
                rnd = run_policy(game, "random", seeds, max_steps=300, skip=4)
                self.assertGreater(ora["mean_reward"], rnd["mean_reward"])

    def test_baselines_deterministic(self):
        a = run_policy("dodge", "sticky_random", [5, 6], rng_seed=3)
        b = run_policy("dodge", "sticky_random", [5, 6], rng_seed=3)
        self.assertEqual(a, b)

    def test_breakout_clear_rate_from_reward_stream(self):
        from training.arcade_baselines import summarize_episodes

        rep = summarize_episodes([16.0, 3.0, 2.0], [300, 120, 500], [True, True, False],
                                 [0, 1, 0], "breakout", 500)
        self.assertAlmostEqual(rep["clear_rate"], 1 / 3)
        self.assertAlmostEqual(rep["survival_rate"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
