"""UGA pad: game-agnostic projection, wrapper contract, checkpoint versioning.

No ALE needed (arcade games and fakes only); torch-dependent cases skip
without torch. No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import tempfile
import unittest
from pathlib import Path

import numpy as np

from environment.arcade import ACTION_MEANINGS, GAMES, make_game, make_game_factory
from environment.universal_actions import (
    NUM_PAD_ACTIONS,
    PAD_ACTIONS,
    PAD_VERSION,
    UniversalActionWrapper,
    components,
    projection,
)

try:
    import torch  # noqa: F401

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

try:
    import ale_py  # noqa: F401

    _HAS_ALE = True
except ImportError:
    _HAS_ALE = False


class TestProjection(unittest.TestCase):
    def test_pad_is_the_ale_full_action_set_in_enum_order(self):
        self.assertEqual(NUM_PAD_ACTIONS, 18)
        self.assertEqual(len(set(PAD_ACTIONS)), 18)
        self.assertEqual(len({components(a) for a in PAD_ACTIONS}), 18)

    @unittest.skipUnless(_HAS_ALE, "ale-py not installed")
    def test_pad_matches_ale_enum(self):
        from ale_py import Action

        for i, name in enumerate(PAD_ACTIONS):
            self.assertEqual(Action[name].value, i, name)

    def test_components(self):
        self.assertEqual(components("NOOP"), (0, 0, 0))
        self.assertEqual(components("UPLEFTFIRE"), (-1, -1, 1))
        self.assertEqual(components("DOWNRIGHT"), (1, 1, 0))
        self.assertEqual(components("FIRE"), (0, 0, 1))
        with self.assertRaises(ValueError):
            components("JUMP")

    def test_full_set_is_identity(self):
        table, exact = projection(PAD_ACTIONS)
        self.assertEqual(table, tuple(range(18)))
        self.assertTrue(all(exact))

    def test_arcade_projection(self):
        table, exact = projection(ACTION_MEANINGS)  # NOOP, LEFT, RIGHT
        named = [ACTION_MEANINGS[i] for i in table]
        self.assertEqual(named[PAD_ACTIONS.index("LEFT")], "LEFT")
        self.assertEqual(named[PAD_ACTIONS.index("UPRIGHTFIRE")], "RIGHT")
        self.assertEqual(named[PAD_ACTIONS.index("UP")], "NOOP")
        self.assertEqual(named[PAD_ACTIONS.index("FIRE")], "NOOP")
        self.assertEqual(sum(exact), 3)

    def test_vertical_only_set(self):  # e.g. ALE Freeway: NOOP, UP, DOWN
        names = ("NOOP", "UP", "DOWN")
        table, _ = projection(names)
        self.assertEqual(names[table[PAD_ACTIONS.index("UPLEFT")]], "UP")
        self.assertEqual(names[table[PAD_ACTIONS.index("RIGHT")]], "NOOP")
        self.assertEqual(names[table[PAD_ACTIONS.index("DOWNFIRE")]], "DOWN")

    def test_projection_depends_on_names_not_order(self):
        names = ("NOOP", "FIRE", "RIGHT", "LEFT", "RIGHTFIRE", "LEFTFIRE")
        perm = (3, 0, 5, 1, 4, 2)
        shuffled = tuple(names[i] for i in perm)
        a, _ = projection(names)
        b, _ = projection(shuffled)
        self.assertEqual([names[i] for i in a], [shuffled[i] for i in b])


class TestWrapper(unittest.TestCase):
    def test_wraps_every_arcade_game(self):
        for g in GAMES:
            env = UniversalActionWrapper(make_game_factory(g, obs_size=48, skip=2, max_steps=50)())
            self.assertEqual(env.action_space.n, 18)
            self.assertEqual(env.uga_action_space, PAD_VERSION)
            self.assertEqual(int(env.legal_mask.sum()), 3)
            obs, info = env.reset(seed=1)
            rng = np.random.default_rng(0)
            for _ in range(20):
                obs, r, term, trunc, info = env.step(int(rng.integers(0, 18)))
                self.assertEqual(info, {})
                if term or trunc:
                    env.reset()
            env.close()

    def test_pad_step_equals_native_step(self):
        a = UniversalActionWrapper(make_game_factory("catch", obs_size=48)())
        b = make_game_factory("catch", obs_size=48)()
        oa, _ = a.reset(seed=3)
        ob, _ = b.reset(seed=3)
        np.testing.assert_array_equal(oa, ob)
        for pad in ("LEFT", "UPLEFTFIRE", "FIRE", "RIGHT", "DOWNRIGHT"):
            native = {"LEFT": 1, "UPLEFTFIRE": 1, "FIRE": 0, "RIGHT": 2, "DOWNRIGHT": 2}[pad]
            ra = a.step(PAD_ACTIONS.index(pad))
            rb = b.step(native)
            np.testing.assert_array_equal(ra[0], rb[0])
            self.assertEqual(ra[1:4], rb[1:4])

    def test_invalid_actions_rejected(self):
        env = UniversalActionWrapper(make_game_factory("pong", obs_size=48)())
        env.reset(seed=0)
        for bad in (18, -1, True, 1.0, "UP"):
            with self.assertRaises(ValueError):
                env.step(bad)

    def test_reward_semantics_forwarded(self):
        from environment.preprocessing import PreprocessingWrapper
        from environment.toy_pong import ToyPongEnv

        class Named(ToyPongEnv):
            action_meanings = ACTION_MEANINGS

        self.assertEqual(UniversalActionWrapper(PreprocessingWrapper(Named())).reward_semantics, "sign")
        self.assertEqual(UniversalActionWrapper(make_game_factory("catch")()).reward_semantics, "generic")

    def test_env_without_meanings_is_refused(self):
        from environment.preprocessing import PreprocessingWrapper
        from environment.toy_pong import ToyPongEnv

        with self.assertRaises(ValueError):
            UniversalActionWrapper(PreprocessingWrapper(ToyPongEnv()))

    def test_raw_game_meanings(self):
        self.assertEqual(make_game("dodge").action_meanings, ACTION_MEANINGS)


class TestRewardTransform(unittest.TestCase):
    def test_sign_clip_is_training_only(self):
        try:
            import gymnasium as gym
        except ImportError:  # pragma: no cover
            self.skipTest("gymnasium not installed")
        from environment.atari_env import RewardTransform

        class Score(gym.Env):
            action_space = gym.spaces.Discrete(2)
            observation_space = gym.spaces.Box(0, 1, (1,), np.float32)

            def reset(self, *, seed=None, options=None):
                return np.zeros(1, np.float32), {}

            def step(self, action):
                return np.zeros(1, np.float32), [-7.0, 25.0][action], False, False, {}

        clipped = RewardTransform(Score(), "sign")
        clipped.reset()
        self.assertEqual(clipped.step(0)[1], -1.0)
        self.assertEqual(clipped.step(1)[1], 1.0)
        raw = RewardTransform(Score(), "none")
        self.assertEqual(raw.step(1)[1], 25.0)
        with self.assertRaises(ValueError):
            RewardTransform(Score(), "running")


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestCheckpointVersion(unittest.TestCase):
    def _trainer(self, pad: bool, ckpt_dir: str):
        from agent.model import ActorCritic
        from training.ppo import PPOConfig, PPOTrainer

        env = make_game_factory("catch", obs_size=48, max_steps=30)()
        if pad:
            env = UniversalActionWrapper(env)
        model = ActorCritic(num_actions=int(env.action_space.n), frame_size=48)
        cfg = PPOConfig(rollout_length=8, minibatch_size=8, update_epochs=1, total_timesteps=8,
                        checkpoint_dir=ckpt_dir, checkpoint_every_updates=0)
        return PPOTrainer(env, model, cfg), env

    def test_pad_checkpoint_is_versioned_and_guarded(self):
        from training.ppo import PPOTrainer

        with tempfile.TemporaryDirectory() as tmp:
            trainer, _ = self._trainer(True, tmp)
            trainer.train()
            path = Path(tmp) / "ppo_final.pt"
            payload = torch.load(path, weights_only=True)
            self.assertEqual(payload["action_space"], PAD_VERSION)
            self.assertEqual(payload["model_config"]["num_actions"], 18)
            pad_env = UniversalActionWrapper(make_game_factory("catch", obs_size=48)())
            self.assertEqual(PPOTrainer.load_checkpoint(path, pad_env).model._config["num_actions"], 18)
            with self.assertRaises(ValueError):
                PPOTrainer.load_checkpoint(path, make_game_factory("catch", obs_size=48)())

    def test_legacy_checkpoint_has_no_key_and_refuses_pad_env(self):
        from training.ppo import PPOTrainer

        with tempfile.TemporaryDirectory() as tmp:
            trainer, _ = self._trainer(False, tmp)
            trainer.train()
            path = Path(tmp) / "ppo_final.pt"
            self.assertNotIn("action_space", torch.load(path, weights_only=True))
            PPOTrainer.load_checkpoint(path, make_game_factory("catch", obs_size=48)())
            with self.assertRaises(ValueError):
                PPOTrainer.load_checkpoint(
                    path, UniversalActionWrapper(make_game_factory("catch", obs_size=48)()))

    def test_arcade_suite_runs_on_the_pad(self):
        from training.generalization import load_config, train_run

        cfg = load_config()
        cfg["env"]["action_space"] = "universal"
        cfg["env"]["max_steps"] = 40
        cfg["eval"]["episodes"] = 1
        cfg["ppo"].update(rollout_length=16, minibatch_size=8, update_epochs=1)
        cfg["budgets"] = {"specialist": 48}
        with tempfile.TemporaryDirectory() as tmp:
            res = train_run(cfg, "specialist", 0, Path(tmp), game="pong")
            payload = torch.load(Path(tmp) / "runs" / "specialist_pong_s0" / "ckpt" / "ppo_final.pt",
                                 weights_only=True)
        self.assertEqual(payload["model_config"]["num_actions"], 18)
        self.assertEqual(payload["action_space"], PAD_VERSION)
        self.assertEqual(set(res["final_eval"]), set(GAMES))
        self.assertTrue(all(0 <= int(a) < 18 for a in res["final_eval"]["pong"]["action_counts"]))


if __name__ == "__main__":
    unittest.main()
