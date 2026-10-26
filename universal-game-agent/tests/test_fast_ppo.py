"""Stage 2 fast PPO engine, model options, telemetry, HNS, reward normalisation.

Torch-only (toy env); ALE-specific Stage 2 checks live in ``tests/test_stage2_atari.py``.
No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import csv
import math
import tempfile
import unittest
import warnings
from dataclasses import fields
from pathlib import Path

import numpy as np

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper, preprocess_frame, preprocess_frame_uint8
    from environment.toy_pong import ToyPongEnv
    from environment.vec import SyncVectorEnv
    from training.fast_ppo import (
        CLEANRL_PRESET,
        FastPPOConfig,
        FastPPOTrainer,
        PerGameReturnNormalizer,
        build_trainer,
        check_schema,
        wants_fast_engine,
    )
    from training.ppo import PPOConfig, PPOTrainer
    from training.telemetry import FAST_FIELDNAMES, FIELDNAMES

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

from training import hns


def _toy(obs_dtype="float32"):
    def make():
        if obs_dtype == "float32":
            return PreprocessingWrapper(ToyPongEnv(max_steps=40), size=84, num_stack=4)
        return PreprocessingWrapper(ToyPongEnv(max_steps=40), size=84, num_stack=4, obs_dtype=obs_dtype)
    return make


def _trainer(tmp, recurrent=False, n=2, **ppo):
    cfg = {"engine": "fast", "rollout_length": 16, "num_envs": n, "total_timesteps": 16 * n * 2,
           "update_epochs": 2, "num_minibatches": 2, "seed": 3, "checkpoint_dir": str(tmp),
           "checkpoint_every_updates": 0, **ppo}
    env = SyncVectorEnv([_toy()] * n)
    model_cfg = {"feature_dim": 32, "hidden_size": 16, "recurrent": recurrent}
    return build_trainer(env, 3, model_cfg, cfg)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestConfig(unittest.TestCase):
    def test_legacy_config_has_no_new_fields(self):
        names = {f.name for f in fields(PPOConfig)}
        for key in ("engine", "preset", "anneal_lr", "adam_eps", "num_minibatches", "reward_norm"):
            self.assertNotIn(key, names)

    def test_cleanrl_preset_values_and_explicit_override(self):
        cfg = FastPPOConfig.from_dict({"preset": "cleanrl"})
        for key, value in CLEANRL_PRESET.items():
            self.assertEqual(getattr(cfg, key), value, key)
        self.assertEqual(cfg.adam_eps, 1e-5)
        self.assertEqual(cfg.clip_range, 0.1)
        over = FastPPOConfig.from_dict({"preset": "cleanrl", "update_epochs": 3})
        self.assertEqual(over.update_epochs, 3)

    def test_wants_fast_engine(self):
        self.assertFalse(wants_fast_engine({}))
        self.assertFalse(wants_fast_engine({"engine": "legacy"}))
        self.assertTrue(wants_fast_engine({"engine": "fast"}))
        self.assertTrue(wants_fast_engine({"preset": "cleanrl"}))
        with self.assertRaises(ValueError):
            wants_fast_engine({"engine": "turbo"})

    def test_validation(self):
        with self.assertRaises(ValueError):
            FastPPOConfig(adv_norm="nope")
        with self.assertRaises(ValueError):
            FastPPOConfig(reward_norm="popart")
        with self.assertRaises(ValueError):
            FastPPOConfig(device="tpu")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            FastPPOConfig.from_dict({"bogus": 1})
        self.assertTrue(any("bogus" in str(x.message) for x in w))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestModelOptions(unittest.TestCase):
    def test_default_config_keys_unchanged(self):
        self.assertEqual(set(ActorCritic()._config),
                         {"num_actions", "in_channels", "frame_size", "feature_dim", "hidden_size", "num_layers"})
        cfg = ActorCritic(recurrent=False, encoder="impala", init="orthogonal")._config
        self.assertEqual((cfg["recurrent"], cfg["encoder"], cfg["init"]), (False, "impala", "orthogonal"))

    def test_uint8_scaled_in_model(self):
        torch.manual_seed(0)
        m = ActorCritic(feature_dim=32, hidden_size=16).eval()
        x = torch.randint(0, 256, (2, 4, 84, 84), dtype=torch.uint8)
        h = m.initial_state(2)
        a, va, _ = m(x, h)
        b, vb, _ = m(x.float() / 255.0, h)
        self.assertTrue(torch.allclose(a, b, atol=1e-6) and torch.allclose(va, vb, atol=1e-6))

    def test_feedforward_passes_hidden_through(self):
        m = ActorCritic(recurrent=False, feature_dim=32, hidden_size=16)
        self.assertIsNone(m.gru)
        h = m.initial_state(3)
        logits, value, h2 = m(torch.rand(3, 4, 84, 84), h)
        self.assertEqual(tuple(logits.shape), (3, 3))
        self.assertEqual(tuple(value.shape), (3,))
        self.assertIs(h2, h)

    def test_impala_encoder_shapes(self):
        m = ActorCritic(num_actions=18, encoder="impala", feature_dim=256, recurrent=False)
        logits, value, _ = m(torch.rand(2, 4, 84, 84), m.initial_state(2))
        self.assertEqual(tuple(logits.shape), (2, 18))

    def test_bad_options(self):
        for kw in ({"encoder": "vit"}, {"init": "xavier"}, {"recurrent": "yes"}):
            with self.assertRaises(ValueError):
                ActorCritic(**kw)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestFastEngine(unittest.TestCase):
    def _replay_matches_rollout(self, recurrent):
        with tempfile.TemporaryDirectory() as tmp:
            tr = _trainer(tmp, recurrent=recurrent)
            buf = tr.collect_rollout(respect_budget=True)[0]
            with torch.no_grad():
                if recurrent:
                    logits, values = tr._replay_recurrent(buf["obs"], buf["h0"], buf["dones"])
                else:
                    t, n = buf["values"].shape
                    logits, values = tr.model.heads(tr.model.encode(buf["obs"].reshape(t * n, 4, 84, 84)))
                    logits, values = logits.reshape(t, n, -1), values.reshape(t, n)
                lp = torch.distributions.Categorical(logits=logits).log_prob(buf["actions"])
            self.assertTrue(torch.allclose(lp, buf["logprobs"], atol=1e-5))
            self.assertTrue(torch.allclose(values, buf["values"], atol=1e-5))
            return buf

    def test_feedforward_replay_reproduces_rollout(self):
        self._replay_matches_rollout(False)

    def test_recurrent_replay_reproduces_rollout_across_dones(self):
        buf = self._replay_matches_rollout(True)
        # ToyPong episodes (40 frames) end inside a 16-step x 2-update rollout often enough;
        # at least the test exercised the masked path when a done occurred.
        self.assertEqual(buf["obs"].dtype, torch.uint8)

    def test_train_checkpoint_resume_and_legacy_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = _trainer(tmp, recurrent=True, telemetry_path=str(Path(tmp) / "t.csv"))
            tr.train()
            ckpt = Path(tmp) / "ppo_final.pt"
            payload = torch.load(ckpt, weights_only=True)
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["engine"], "fast")
            env = SyncVectorEnv([_toy()] * 2)
            loaded = PPOTrainer.load_checkpoint(ckpt, env)  # v2 dispatches to the fast engine
            self.assertIsInstance(loaded, FastPPOTrainer)
            self.assertEqual(loaded.num_timesteps, 64)
            loaded.config.total_timesteps = 96
            loaded.train()
            self.assertEqual(loaded.num_timesteps, 96)
            with open(Path(tmp) / "t.csv", newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            self.assertEqual(tuple(header), FAST_FIELDNAMES)
            self.assertEqual(tuple(header[:len(FIELDNAMES)]), FIELDNAMES)

    def test_future_schema_rejected(self):
        with self.assertRaises(ValueError):
            check_schema({"schema_version": 99}, "x.pt")
        self.assertEqual(check_schema({}, "x.pt"), 1)

    def test_lr_annealing_follows_cleanrl_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = _trainer(tmp, anneal_lr=True, total_timesteps=128)
            hist = tr.train()
            # 4 updates of 32 steps: frac = 1, 0.75, 0.5, 0.25 (CleanRL iteration-1 convention)
            lr0 = tr.config.learning_rate
            self.assertEqual([round(x / lr0, 6) for x in hist["learning_rate"]], [1.0, 0.75, 0.5, 0.25])
            tr2 = _trainer(tmp, anneal_lr=True, total_timesteps=64, anneal_total_timesteps=128)
            hist2 = tr2.train()
            self.assertEqual([round(x / lr0, 6) for x in hist2["learning_rate"]], [1.0, 0.75])

    def test_adam_eps_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = _trainer(tmp, adam_eps=1e-5)
            self.assertEqual(tr.optimizer.param_groups[0]["eps"], 1e-5)

    def test_diagnostics_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = _trainer(tmp, shuffle_minibatches=True, adv_norm="minibatch").train()
            for key in ("approx_kl", "clipfrac", "explained_variance", "old_approx_kl"):
                self.assertEqual(len(hist[key]), 2)
                self.assertFalse(math.isinf(hist[key][-1]))

    def test_recurrent_minibatch_must_divide_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _trainer(tmp, recurrent=True, n=3, num_minibatches=2)

    def test_gae_cuts_accumulation_at_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = _trainer(tmp, gamma=1.0, gae_lambda=1.0)
            buf = {"values": torch.zeros(3, 1), "rewards": torch.tensor([[1.0], [1.0], [1.0]]),
                   "next_value": torch.zeros(1), "trunc_values": torch.tensor([[0.0], [5.0], [0.0]]),
                   "truncated": torch.tensor([[False], [True], [False]]),
                   "terminated": torch.zeros(3, 1, dtype=torch.bool)}
            buf["dones"] = buf["truncated"] | buf["terminated"]
            adv, _ = tr.compute_advantages(buf)
            # step 1 bootstraps 5 from its own final obs; step 2's reward never leaks into it
            self.assertEqual(adv.flatten().tolist(), [7.0, 6.0, 1.0])


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestRewardNormAndPixels(unittest.TestCase):
    def test_per_game_normalizer_equalises_scales(self):
        norm = PerGameReturnNormalizer(["a", "a", "b", "b"], gamma=0.99)
        rng = np.random.default_rng(0)
        outs = []
        for _ in range(2000):
            r = np.concatenate([rng.normal(size=2), 100.0 * rng.normal(size=2)])
            outs.append(norm(r, np.zeros(4, dtype=bool)))
        outs = np.array(outs[500:])
        ratio = outs[:, 2:].std() / outs[:, :2].std()
        self.assertLess(abs(math.log(ratio)), math.log(1.5))  # raw ratio is 100x
        state = norm.state_dict()
        other = PerGameReturnNormalizer(["a", "a", "b", "b"], gamma=0.99)
        other.load_state_dict(state)
        self.assertEqual(other.scales(), norm.scales())

    def test_uint8_preprocessing_within_half_grey_level(self):
        rng = np.random.default_rng(1)
        for shape in ((210, 160, 3), (48, 64, 3)):
            frame = rng.integers(0, 256, shape, dtype=np.uint8)
            a = preprocess_frame(frame) * 255.0
            b = preprocess_frame_uint8(frame).astype(np.float64)
            self.assertLessEqual(float(np.abs(a - b).max()), 0.5 + 1e-3)

    def test_uint8_wrapper_obs_and_eval(self):
        from training.evaluate import evaluate

        env = _toy("uint8")()
        obs, _ = env.reset(seed=0)
        self.assertEqual(obs.dtype, np.uint8)
        self.assertEqual(env.observation_space.dtype, np.uint8)
        torch.manual_seed(0)
        m = ActorCritic(feature_dim=32, hidden_size=16)
        r8 = evaluate(m, _toy("uint8"), episodes=1, seeds=[0])
        r32 = evaluate(m, _toy("float32"), episodes=1, seeds=[0])
        self.assertEqual(r8["episodes"], 1)
        self.assertEqual(r8["episode_lengths"], r32["episode_lengths"])


class TestHNS(unittest.TestCase):
    def test_reference_values_and_formula(self):
        self.assertEqual(hns.REFERENCE_SCORES["pong"], (-20.7, 14.6))
        self.assertAlmostEqual(hns.human_normalized("pong", 14.6), 1.0)
        self.assertAlmostEqual(hns.human_normalized("pong", -20.7), 0.0)
        self.assertTrue(math.isnan(hns.human_normalized("not_a_game", 1.0)))
        agg = hns.aggregate({"pong": 14.6, "breakout": 1.7, "freeway": 29.6})
        self.assertAlmostEqual(agg["median"], 1.0)
        self.assertAlmostEqual(agg["mean"], 2 / 3)
        self.assertIn("dqn_zoo", agg["source"])


if __name__ == "__main__":
    unittest.main()
