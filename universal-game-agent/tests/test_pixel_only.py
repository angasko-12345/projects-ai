"""The learning path sees rendered pixels only.

* Policy input is a pure function of the rendered frames: the wrapper's obs
  equals a fresh ``FrameStack`` replay of ``env.render()`` frames.
* Hidden state that is not drawn does not reach the obs.
* ``info`` contents and non-rendered env state never change what PPO stores
  or what ``evaluate`` measures (two envs with identical pixels but different
  secrets produce identical rollouts).
* The model's forward takes nothing but the obs tensor and its own hidden state.
* The multi-game vector env never contains the held-out games.
No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import copy
import inspect
import tempfile
import unittest

import numpy as np

from environment.arcade import FAR_HELDOUT_GAME, GAMES, HELDOUT_GAME, make_game
from environment.preprocessing import FrameStack, PreprocessingWrapper
from environment.toy_pong import _Base, spaces

try:
    import torch

    from agent.model import ActorCritic
    from environment.vec import SyncVectorEnv
    from training.evaluate import evaluate
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class SecretGame(_Base):
    """Frames depend only on (step, action); secrets live in attrs and ``info``."""

    def __init__(self, secret):
        super().__init__()
        self.secret = secret
        self._hidden_velocity = secret * 7.0
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(0, 255, (48, 64, 3), np.uint8)
        self.t = 0
        self.x = 30

    def _frame(self):
        f = np.zeros((48, 64, 3), dtype=np.uint8)
        f[40:42, self.x:self.x + 6] = 200
        f[(self.t * 3) % 40, (self.t * 5) % 60] = 255
        return f

    def reset(self, *, seed=None, options=None):
        self.t, self.x = 0, 30
        return self._frame(), {"secret": self.secret, "ball_x": self.secret}

    def step(self, action):
        self.t += 1
        self.x = int(np.clip(self.x + (-2 if action == 1 else 2 if action == 2 else 0), 0, 58))
        self._hidden_velocity += self.secret
        reward = 1.0 if self.t % 9 == 0 else 0.0
        terminated = self.t % 37 == 0
        return self._frame(), reward, terminated, False, {
            "secret": self.secret, "ball_x": self._hidden_velocity, "score": self.secret * self.t}

    def close(self):
        pass


class TestObsIsPureFunctionOfPixels(unittest.TestCase):
    def test_wrapper_obs_equals_replay_of_rendered_frames(self):
        rng = np.random.default_rng(0)
        for name in GAMES:
            with self.subTest(game=name):
                raw = make_game(name)
                env = PreprocessingWrapper(raw, size=48, num_stack=4)
                obs, _ = env.reset(seed=4)
                replay = FrameStack(4, 48)
                np.testing.assert_array_equal(obs, replay.reset(raw.render()))
                for _ in range(120):
                    obs, _, term, trunc, info = env.step(int(rng.integers(0, 3)))
                    self.assertEqual(info, {})
                    np.testing.assert_array_equal(obs, replay.push(raw.render()))
                    if term or trunc:
                        break

    def test_non_rendered_state_does_not_reach_obs(self):
        # Two envs, same seed -> same pixels; perturb state that is not drawn.
        for name, attr in (("catch", "_fvy"), ("breakout", "_vx"), ("volley", "_g"),
                           ("dodge", "_spawn_gap"), ("pong", "_vx")):
            with self.subTest(game=name):
                a, b = make_game(name), make_game(name)
                fa, _ = a.reset(seed=9)
                fb, _ = b.reset(seed=9)
                setattr(b, attr, getattr(b, attr) * 0.5 + 0.25)
                np.testing.assert_array_equal(fa, b.render())
                np.testing.assert_array_equal(FrameStack(4, 48).reset(fa),
                                              FrameStack(4, 48).reset(b.render()))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestLearningPathIgnoresSecrets(unittest.TestCase):
    def _rollout(self, secrets):
        torch.manual_seed(0)
        model = ActorCritic(num_actions=3, frame_size=48)
        with tempfile.TemporaryDirectory() as tmp:
            env = SyncVectorEnv(
                [(lambda s=s: PreprocessingWrapper(SecretGame(s), size=48)) for s in secrets],
                check_spaces=True)
            cfg = PPOConfig(rollout_length=40, minibatch_size=20, update_epochs=1,
                            total_timesteps=80 * len(secrets), num_envs=len(secrets),
                            checkpoint_dir=tmp, checkpoint_every_updates=0)
            trainer = PPOTrainer(env, model, cfg)
            buf = trainer.collect_rollout()[0]
            trainer.update(buf)
            buf2 = trainer.collect_rollout()[0]
        return buf, buf2, model

    def test_rollout_and_update_identical_under_different_secrets(self):
        a1, a2, ma = self._rollout([1, 2])
        b1, b2, mb = self._rollout([1000, -55])
        for key in ("obs", "actions", "logprobs", "values", "ext", "dones"):
            self.assertTrue(torch.equal(a1[key], b1[key]), key)
            self.assertTrue(torch.equal(a2[key], b2[key]), f"after update: {key}")
        for k, v in ma.state_dict().items():
            self.assertTrue(torch.equal(v, mb.state_dict()[k]), k)

    def test_evaluate_identical_under_different_secrets(self):
        torch.manual_seed(1)
        model = ActorCritic(num_actions=3, frame_size=48)
        ra = evaluate(model, lambda: PreprocessingWrapper(SecretGame(3), size=48), episodes=2)
        rb = evaluate(copy.deepcopy(model), lambda: PreprocessingWrapper(SecretGame(-9), size=48), episodes=2)
        self.assertEqual(ra, rb)

    def test_model_forward_takes_only_obs_and_hidden(self):
        params = list(inspect.signature(ActorCritic.forward).parameters)
        self.assertEqual(params, ["self", "obs", "hidden"])
        params = list(inspect.signature(ActorCritic.forward_sequence).parameters)
        self.assertEqual(params, ["self", "obs_seq", "hidden"])


class TestMultiGameSplit(unittest.TestCase):
    def test_multi_slots_round_robin_over_training_games_only(self):
        from training.generalization import slot_games

        self.assertEqual(slot_games("multi", None, 3), ["pong", "catch", "breakout"])
        self.assertEqual(slot_games("multi", None, 6), ["pong", "catch", "breakout"] * 2)
        for n in (1, 2, 3, 7):
            slots = slot_games("multi", None, n)
            self.assertNotIn(HELDOUT_GAME, slots)
            self.assertNotIn(FAR_HELDOUT_GAME, slots)
        self.assertEqual(slot_games("scratch", "volley", 3), ["volley"] * 3)
        with self.assertRaises(ValueError):
            slot_games("specialist", None, 3)


if __name__ == "__main__":
    unittest.main()
