"""ALE adapter: contract, explicit reset vs ALE autoreset, pixel-only, config wiring.

Every test here needs the optional ``ale-py`` extra (``requirements-atari.txt``)
and skips cleanly without it. No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import ale_py  # noqa: F401

    _HAS_ALE = True
except ImportError:
    _HAS_ALE = False

try:
    import torch

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

PILOT_GAMES = ("pong", "breakout", "space_invaders", "demon_attack", "freeway")


@unittest.skipUnless(_HAS_ALE, "ale-py not installed (optional requirements-atari.txt)")
class TestAtariGameContract(unittest.TestCase):
    def test_pilot_games_satisfy_contract(self):
        from environment.atari_env import AtariGame, check_atari_env

        for g in PILOT_GAMES:
            env = AtariGame(g)
            try:
                check_atari_env(env, seed=0, steps=120)
                self.assertEqual(env.action_meanings[0], "NOOP")
            finally:
                env.close()

    def test_info_is_always_empty_even_with_lives(self):
        from environment.atari_env import AtariGame

        env = AtariGame("breakout")  # ALE reports lives=5 here; it must not leak
        _, info = env.reset(seed=1)
        self.assertEqual(info, {})
        for _ in range(50):
            _, _, term, trunc, info = env.step(1)
            self.assertEqual(info, {})
        env.close()

    def test_seeded_reset_is_reproducible(self):
        from environment.atari_env import AtariGame

        def roll(seed):
            env = AtariGame("pong", sticky=0.25)
            frames = [env.reset(seed=seed)[0]]
            rng = np.random.default_rng(7)
            for _ in range(60):
                frames.append(env.step(int(rng.integers(0, 6)))[0])
            env.close()
            return np.stack(frames)

        np.testing.assert_array_equal(roll(3), roll(3))
        self.assertFalse(np.array_equal(roll(3), roll(4)))

    def test_validation(self):
        from environment.atari_env import AtariGame

        with self.assertRaises(ValueError):
            AtariGame("pong", sticky=1.5)
        with self.assertRaises(ValueError):
            AtariGame("pong", max_episode_frames=0)
        env = AtariGame("pong")
        env.reset(seed=0)
        for bad in (6, -1, True, 0.0):
            with self.assertRaises(ValueError):
                env.step(bad)
        env.close()


@unittest.skipUnless(_HAS_ALE, "ale-py not installed (optional requirements-atari.txt)")
class TestExplicitResetContract(unittest.TestCase):
    """ALE is an autoresetting world; UGA never autoresets. Both sides tested."""

    def test_step_before_reset_raises(self):
        from environment.atari_env import AtariGame

        env = AtariGame("pong")
        with self.assertRaises(RuntimeError):
            env.step(0)
        env.close()

    def test_truncation_then_no_autoreset(self):
        from environment.atari_env import AtariGame

        env = AtariGame("pong", max_episode_frames=200, noop_max=0)
        env.reset(seed=0)
        flags = []
        for _ in range(400):
            obs, _, term, trunc, _ = env.step(0)
            flags.append((term, trunc))
            if term or trunc:
                break
        self.assertEqual(flags[-1], (False, True))  # time cap, not game over
        self.assertTrue(all(f == (False, False) for f in flags[:-1]))
        self.assertLessEqual(len(flags), 200)
        np.testing.assert_array_equal(obs, env.render())  # terminal frame untouched
        with self.assertRaises(RuntimeError):  # no silent continuation / autoreset
            env.step(0)
        obs2, info = env.reset()
        self.assertEqual(info, {})
        env.step(0)  # explicit reset makes it steppable again
        env.close()

    def test_preprocessed_pipeline_stops_at_the_boundary(self):
        from environment.atari_env import make_atari_factory

        env = make_atari_factory("pong", max_episode_frames=64, noop_max=0)()
        env.reset(seed=0)
        n = 0
        while True:
            _, _, term, trunc, _ = env.step(0)
            n += 1
            if term or trunc:
                break
        self.assertEqual((term, trunc, n), (False, True, 16))  # 64 frames / skip 4
        with self.assertRaises(RuntimeError):
            env.step(0)
        env.close()

    def test_ale_vector_backend_presents_no_autoreset(self):
        from environment.atari_env import AtariVectorEnvAdapter

        vec = AtariVectorEnvAdapter("pong", 2, max_episode_frames=40, noop_max=0, num_threads=1)
        try:
            obs, infos = vec.reset(seeds=[0, 1])
            self.assertEqual(infos, [{}, {}])
            self.assertEqual(obs[0].shape, (4, 84, 84))
            for _ in range(20):
                obs, rew, term, trunc, infos = vec.step([0, 0])
                if any(trunc) or any(term):
                    break
            self.assertEqual(trunc, [True, True])
            self.assertEqual(infos, [{}, {}])
            with self.assertRaises(RuntimeError):  # finished slots must be reset first
                vec.step([0, 0])
            fresh, _ = vec.reset_env(0)
            # The terminal obs we got is the old episode's, not ALE's eager reset.
            self.assertFalse(np.array_equal(fresh, obs[0]))
            with self.assertRaises(RuntimeError):  # only a just-finished slot can be reset
                vec.reset_env(0)
            vec.reset_env(1)
            vec.step([0, 0])
        finally:
            vec.close()


@unittest.skipUnless(_HAS_ALE, "ale-py not installed (optional requirements-atari.txt)")
class TestAtariPixelOnly(unittest.TestCase):
    """Model input is a pure function of rendered RGB frames; RAM/info never reach it."""

    def test_obs_is_a_replay_of_rendered_frames(self):
        from environment.atari_env import make_atari_factory
        from environment.preprocessing import FrameStack

        for game in ("pong", "breakout"):
            env = make_atari_factory(game, obs_size=84, num_stack=4, skip=4, sticky=0.25,
                                     reward_transform="sign")()
            raw = env.unwrapped
            frames: list = []
            real_step = raw.step

            def recording_step(action, _real=real_step, _frames=frames):
                out = _real(action)
                _frames.append(out[0].copy())
                return out

            raw.step = recording_step
            obs, info = env.reset(seed=5)
            self.assertEqual(info, {})
            stack = FrameStack(num_stack=4, size=84)
            np.testing.assert_array_equal(obs, stack.reset(raw.render()))
            rng = np.random.default_rng(0)
            for _ in range(80):
                frames.clear()
                obs, _, term, trunc, info = env.step(int(rng.integers(0, 18)))
                self.assertEqual(info, {})
                pooled = np.maximum(frames[-2], frames[-1]) if len(frames) > 1 else frames[-1]
                np.testing.assert_array_equal(obs, stack.push(pooled))
                if term or trunc:
                    break
            env.close()

    def test_ram_and_lives_are_not_in_the_obs(self):
        from environment.atari_env import make_atari_factory

        env = make_atari_factory("breakout")()
        obs, info = env.reset(seed=0)
        self.assertEqual(info, {})
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        ram = env.unwrapped._ale_env.ale.getRAM()
        self.assertEqual(ram.size, 128)
        # obs holds 4*84*84 pixel values in [0,1]; no slot for the 128 RAM bytes.
        self.assertTrue(0.0 <= float(obs.min()) and float(obs.max()) <= 1.0)
        env.close()


@unittest.skipUnless(_HAS_ALE, "ale-py not installed (optional requirements-atari.txt)")
class TestAtariWiring(unittest.TestCase):
    def test_pad_over_ale_minimal_sets(self):
        from environment.atari_env import make_atari_factory

        exact = {"pong": 6, "breakout": 4, "space_invaders": 6, "demon_attack": 6, "freeway": 3}
        for g, n in exact.items():
            env = make_atari_factory(g)()
            self.assertEqual(env.action_space.n, 18)
            self.assertEqual(int(env.legal_mask.sum()), n, g)
            self.assertEqual(env.uga_action_space, "uga-pad18-v1")
            env.close()

    def test_config_factory_registers_atari(self):
        from environment.atari_env import AtariVectorEnvAdapter
        from environment.vec import SyncVectorEnv
        from training.experiment import make_env_for_training, make_env_from_config

        cfg = {"type": "atari", "game": "pong", "reward_transform": "sign"}
        make = make_env_from_config(cfg)
        env = make()
        self.assertEqual(env.action_space.n, 18)
        self.assertEqual(env.env.mode, "sign")
        env.close()
        ev = make_env_from_config(cfg, train=False)()
        self.assertNotIn("RewardTransform", repr(ev))
        ev.close()
        vec = make_env_for_training(make, 2)
        self.assertIsInstance(vec, SyncVectorEnv)
        self.assertNotIsInstance(vec, AtariVectorEnvAdapter)
        vec.close()
        fast = make_env_for_training(make_env_from_config({**cfg, "vector_backend": "ale"}), 2)
        self.assertIsInstance(fast, AtariVectorEnvAdapter)
        fast.close()
        with self.assertRaises(ValueError):
            make_env_from_config({"type": "atari"})
        with self.assertRaises(ValueError):
            make_env_from_config({**cfg, "vector_backend": "envpool"})

    def test_shipped_configs_load(self):
        from configs import load_config
        from training.generalization import load_config as load_gen
        from training.generalization import suite_of

        root = Path(__file__).resolve().parent.parent
        pong = load_config(root / "configs" / "atari_pong.yaml")
        self.assertEqual(pong["env"]["type"], "atari")
        self.assertEqual(pong["model"]["frame_size"], pong["env"]["obs_size"])
        self.assertEqual(pong["ppo"]["total_timesteps"] % pong["ppo"]["num_envs"], 0)
        pilot = load_gen(root / "experiments" / "stage1" / "atari_pilot.yaml")
        s = suite_of(pilot)
        self.assertEqual(s.kind, "atari")
        self.assertEqual(len(s.train), 3)
        self.assertNotIn(s.heldout, s.train)
        self.assertNotIn(s.far_heldout, s.train)
        unit = pilot["ppo"]["num_envs"] * pilot["ppo"]["rollout_length"]
        for arm in ("multi", "specialist", "scratch", "finetune"):
            self.assertEqual(pilot["budgets"][arm] % unit, 0, arm)


@unittest.skipUnless(_HAS_ALE and _HAS_TORCH, "ale-py and torch needed")
class TestAtariTraining(unittest.TestCase):
    def test_tiny_ppo_on_two_atari_slots(self):
        from agent.model import ActorCritic
        from environment.atari_env import make_atari_factory
        from environment.vec import SyncVectorEnv
        from training.ppo import PPOConfig, PPOTrainer

        torch.manual_seed(0)
        env = SyncVectorEnv([make_atari_factory("pong", obs_size=48, reward_transform="sign"),
                             make_atari_factory("breakout", obs_size=48, reward_transform="sign")],
                            check_spaces=True)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PPOConfig(rollout_length=8, minibatch_size=4, update_epochs=1, total_timesteps=32,
                            num_envs=2, checkpoint_dir=tmp, checkpoint_every_updates=0)
            trainer = PPOTrainer(env, ActorCritic(num_actions=18, frame_size=48), cfg)
            hist = trainer.train()
            payload = torch.load(Path(tmp) / "ppo_final.pt", weights_only=True)
        env.close()
        self.assertEqual(trainer.num_timesteps, 32)
        self.assertEqual(len(hist["timesteps"]), 2)
        self.assertEqual(payload["action_space"], "uga-pad18-v1")

    def test_generalization_runner_on_atari_suite(self):
        from training.generalization import load_config, train_run

        root = Path(__file__).resolve().parent.parent
        cfg = load_config(root / "experiments" / "stage1" / "atari_pilot.yaml")
        cfg["env"].update(obs_size=48, max_episode_frames=120)
        cfg["model"]["frame_size"] = 48
        cfg["eval"]["episodes"] = 1
        cfg["ppo"].update(rollout_length=8, minibatch_size=4, update_epochs=1, num_envs=3)
        cfg["budgets"] = {"multi": 48}
        with tempfile.TemporaryDirectory() as tmp:
            res = train_run(cfg, "multi", 0, Path(tmp))
        self.assertEqual(res["slot_games"], ["pong", "breakout", "space_invaders"])
        self.assertEqual(set(res["final_eval"]), set(PILOT_GAMES))


if __name__ == "__main__":
    unittest.main()
