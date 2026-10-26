"""UGA U1: PPO throughput knobs and CleanRL-style details, all opt-in.

Covers the config surface (legacy defaults, validation), uint8 input
normalisation, the batched masked-GRU vector update against the slot-loop
update, inference-mode rollouts, Adam epsilon, linear LR annealing, the
appended telemetry columns, and async-vs-sync PPO on the same seeds.
num_envs=1 byte-identity is guarded by tests/test_vec_ppo_equivalence.py,
which this change leaves untouched. No window, no SendInput.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import contextlib
import copy
import io
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import torch

    from agent.model import ActorCritic, obs_tensor
    from environment.preprocessing import PreprocessingWrapper, quantize_uint8
    from environment.toy_pong import ToyPongEnv
    from environment.vec import AsyncVectorEnv, SyncVectorEnv
    from training.experiment import ConfigEnvFactory
    from training.ppo import PPOConfig, PPOTrainer, explained_variance
    from training.telemetry import FIELDNAMES

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

OBS_SHAPE = (4, 84, 84)
TOY_CFG = {"width": 64, "height": 48, "max_steps": 30, "obs_size": 84, "num_stack": 4, "skip": 1}


class ScriptedEnv:
    """Deterministic fake with scripted (reward, terminated, truncated) per step."""

    def __init__(self, script, base=0.0):
        self.script = list(script)
        self.base = base
        self.t = 0
        self.action_space = type("Space", (), {"n": 3})()

    def reset(self, *, seed=None, options=None):
        self.t = 0
        return np.full(OBS_SHAPE, self.base, dtype=np.float32), {}

    def step(self, action):
        reward, terminated, truncated = (self.script[self.t] if self.t < len(self.script)
                                         else (0.0, False, False))
        self.t += 1
        rng = np.random.default_rng(int(self.base * 1000) + self.t)
        obs = rng.random(OBS_SHAPE, dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

    def close(self):
        pass


def _scripts():
    # Different boundary pattern per slot, including a done on a chunk edge.
    return [
        [(1.0, False, False)] * 3 + [(0.0, True, False)] + [(0.5, False, False)] * 20,
        [(0.0, False, False)] * 7 + [(0.0, False, True)] + [(1.0, False, False)] * 20,
        [(0.0, False, False), (1.0, True, False)] * 12,
    ]


def _trainer(tmp, **ppo):
    scripts = _scripts()
    env = SyncVectorEnv([lambda s=s, i=i: ScriptedEnv(s, base=0.1 * (i + 1))
                         for i, s in enumerate(scripts)])
    torch.manual_seed(0)
    model = ActorCritic(num_actions=3)
    cfg = dict(rollout_length=12, minibatch_size=4, update_epochs=2, total_timesteps=72,
               checkpoint_dir=str(Path(tmp) / "ckpt"), checkpoint_every_updates=0,
               num_envs=3)
    cfg.update(ppo)
    return PPOTrainer(env, model, PPOConfig(**cfg))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestConfig(unittest.TestCase):
    def test_new_knobs_default_to_legacy(self):
        cfg = PPOConfig()
        self.assertEqual(cfg.adam_eps, 1e-8)  # torch.optim.Adam's own default
        self.assertFalse(cfg.anneal_lr)
        self.assertIsNone(cfg.torch_threads)
        self.assertFalse(cfg.inference_mode)
        self.assertFalse(cfg.batched_update)

    def test_validation(self):
        for bad in ({"adam_eps": 0}, {"adam_eps": -1.0}, {"adam_eps": True},
                    {"anneal_lr": "yes"}, {"inference_mode": 1}, {"batched_update": None},
                    {"torch_threads": 0}, {"torch_threads": 2.0}, {"torch_threads": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PPOConfig(**bad)
        PPOConfig(adam_eps=1e-5, anneal_lr=True, torch_threads=2, inference_mode=True,
                  batched_update=True)

    def test_adam_eps_reaches_the_optimizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_trainer(tmp).optimizer.param_groups[0]["eps"], 1e-8)
            self.assertEqual(_trainer(tmp, adam_eps=1e-5).optimizer.param_groups[0]["eps"], 1e-5)

    def test_torch_threads_applied(self):
        before = torch.get_num_threads()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                _trainer(tmp, torch_threads=1)
                self.assertEqual(torch.get_num_threads(), 1)
        finally:
            torch.set_num_threads(before)

    def test_inference_mode_with_curiosity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            trainer = _trainer(tmp)
            with self.assertRaises(ValueError):
                PPOTrainer(trainer.env, trainer.model, PPOConfig(inference_mode=True),
                           curiosity=object())

    def test_toy_fast_config_loads(self):
        from configs import load_config

        cfg = load_config(Path(__file__).resolve().parent.parent / "configs" / "toy_fast.yaml")
        ppo = PPOConfig.from_dict(cfg["ppo"])
        self.assertEqual(ppo.num_envs, 8)
        self.assertEqual(ppo.rollout_length // ppo.minibatch_size, 4)
        self.assertEqual(ppo.total_timesteps % ppo.num_envs, 0)
        self.assertTrue(ppo.batched_update and ppo.inference_mode and ppo.anneal_lr)
        self.assertEqual(ppo.adam_eps, 1e-5)
        self.assertEqual(cfg["env"]["vector_backend"], "async")
        self.assertEqual(cfg["env"]["obs_dtype"], "uint8")


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestUint8Input(unittest.TestCase):
    def test_model_scales_uint8_inside(self):
        torch.manual_seed(0)
        model = ActorCritic(num_actions=3).eval()
        x = torch.randint(0, 256, (2, 4, 84, 84), dtype=torch.uint8)
        h = model.initial_state(2)
        with torch.no_grad():
            a = model(x, h)
            b = model(x.float() / 255.0, h)
        for u, v in zip(a, b, strict=True):
            self.assertTrue(torch.equal(u, v))

    def test_obs_tensor_keeps_uint8_and_legacy_float(self):
        u = np.zeros(OBS_SHAPE, dtype=np.uint8)
        f64 = np.zeros(OBS_SHAPE, dtype=np.float64)
        self.assertEqual(obs_tensor(u).dtype, torch.uint8)
        self.assertEqual(obs_tensor(f64).dtype, torch.float32)

    def test_preprocessing_uint8_is_the_quantised_float_stack(self):
        f = PreprocessingWrapper(ToyPongEnv(), obs_dtype="float32")
        u = PreprocessingWrapper(ToyPongEnv(), obs_dtype="uint8")
        of, _ = f.reset(seed=3)
        ou, _ = u.reset(seed=3)
        self.assertEqual(ou.dtype, np.uint8)
        self.assertTrue(np.array_equal(ou, quantize_uint8(of)))
        for action in (1, 2, 0, 2, 2, 1):
            of = f.step(action)[0]
            ou = u.step(action)[0]
            self.assertTrue(np.array_equal(ou, quantize_uint8(of)))
            self.assertLessEqual(float(np.abs(ou / 255.0 - of).max()), 0.5 / 255 + 1e-6)
        self.assertEqual(u.observation_space.dtype, np.uint8)
        with self.assertRaises(ValueError):
            PreprocessingWrapper(ToyPongEnv(), obs_dtype="float16")

    def test_uint8_training_runs_and_metrics_finite(self):
        env = SyncVectorEnv([ConfigEnvFactory(dict(TOY_CFG, obs_dtype="uint8"))] * 2)
        torch.manual_seed(0)
        with tempfile.TemporaryDirectory() as tmp:
            trainer = PPOTrainer(env, ActorCritic(num_actions=3), PPOConfig(
                rollout_length=16, minibatch_size=8, update_epochs=1, total_timesteps=64,
                num_envs=2, checkpoint_dir=str(Path(tmp) / "c"), checkpoint_every_updates=0,
                batched_update=True))
            with contextlib.redirect_stdout(io.StringIO()):
                history = trainer.train()
        env.close()
        self.assertTrue(all(0.0 <= p <= 1.0 for p in history["pixel_change"]))
        self.assertTrue(all(math.isfinite(x) for x in history["policy_loss"]))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestBatchedUpdate(unittest.TestCase):
    def test_masked_sequence_matches_per_slot_segments(self):
        torch.manual_seed(1)
        model = ActorCritic(num_actions=3).eval()
        steps, slots = 6, 3
        obs = torch.rand(steps, slots, *OBS_SHAPE)
        hidden = torch.randn(1, slots, 128)
        reset = torch.zeros(steps, slots, dtype=torch.bool)
        reset[2, 0] = True
        reset[5, 0] = True
        reset[4, 2] = True
        with torch.no_grad():
            logits, values, h_last = model.forward_masked_sequence(obs, hidden, reset)
            for e in range(slots):
                h = hidden[:, e:e + 1].clone()
                start = 0
                cuts = [t for t in range(1, steps) if reset[t, e]] + [steps]
                for end in cuts:
                    if start > 0:
                        h = torch.zeros_like(h)
                    seg_l, seg_v, h = model.forward_sequence(obs[start:end, e], h)
                    self.assertTrue(torch.allclose(seg_l, logits[start:end, e], atol=1e-5))
                    self.assertTrue(torch.allclose(seg_v, values[start:end, e], atol=1e-5))
                    start = end
                self.assertTrue(torch.allclose(h, h_last[:, e:e + 1], atol=1e-5))

    def test_batched_update_matches_slot_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = _trainer(tmp)
            buf = loop.collect_rollout(respect_budget=True)[0]
            batched = _trainer(tmp, batched_update=True)
            batched.model.load_state_dict(copy.deepcopy(loop.model.state_dict()))
            batched.optimizer.load_state_dict(copy.deepcopy(loop.optimizer.state_dict()))
            batched.num_timesteps = loop.num_timesteps
            # The scripts put dones mid-chunk, on a chunk's last step and as truncations.
            self.assertTrue(bool(buf["terminated"].any()) and bool(buf["truncated"].any()))
            a = loop.update(buf)
            b = batched.update(buf)
        for key in ("policy_loss", "value_loss", "entropy", "approx_kl", "clipfrac"):
            self.assertAlmostEqual(a[key], b[key], places=4, msg=key)
        self.assertEqual(a["explained_variance"], b["explained_variance"])
        for (name, p), q in zip(loop.model.named_parameters(), batched.model.parameters(), strict=True):
            self.assertTrue(torch.allclose(p, q, atol=1e-5), name)

    def test_single_env_ignores_batched_flag(self):
        env = ScriptedEnv([(0.0, False, False)] * 8)
        torch.manual_seed(0)
        with tempfile.TemporaryDirectory() as tmp:
            trainer = PPOTrainer(env, ActorCritic(num_actions=3), PPOConfig(
                rollout_length=4, minibatch_size=2, update_epochs=1, total_timesteps=8,
                checkpoint_dir=tmp, checkpoint_every_updates=0, batched_update=True))
            with contextlib.redirect_stdout(io.StringIO()):
                trainer.train()
        self.assertEqual(trainer.num_updates, 2)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestInferenceModeAndAnneal(unittest.TestCase):
    def _train(self, **ppo):
        with tempfile.TemporaryDirectory() as tmp:
            trainer = _trainer(tmp, **ppo)
            with contextlib.redirect_stdout(io.StringIO()):
                history = trainer.train()
            return trainer, history

    def test_inference_mode_rollout_trains_identically(self):
        a, ha = self._train()
        b, hb = self._train(inference_mode=True)
        for p, q in zip(a.model.parameters(), b.model.parameters(), strict=True):
            self.assertTrue(torch.equal(p, q))
        self.assertEqual(ha["policy_loss"], hb["policy_loss"])

    def test_anneal_lr_is_linear_to_zero(self):
        trainer, history = self._train(anneal_lr=True, learning_rate=1e-3)
        steps_per_update = 12 * 3
        expected = [1e-3 * (1 - (i * steps_per_update) / 72) for i in range(2)]
        self.assertEqual(len(history["learning_rate"]), 2)
        for got, want in zip(history["learning_rate"], expected, strict=True):
            self.assertAlmostEqual(got, want, places=12)
        _, flat = self._train(learning_rate=1e-3)
        self.assertEqual(flat["learning_rate"], [1e-3, 1e-3])

    def test_legacy_shaped_record_still_writes(self):
        import csv

        from training.telemetry import PPOUpdateCSVWriter

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            legacy = {key: 1 for key in FIELDNAMES[:-4]}
            PPOUpdateCSVWriter(path).append(legacy)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["approx_kl"], "")
            self.assertEqual(rows[0]["timesteps"], "1")
            with self.assertRaises(KeyError):
                PPOUpdateCSVWriter(path).append({"fps": 1})

    def test_diagnostics_in_history_and_csv_tail(self):
        _, history = self._train()
        for key in ("approx_kl", "clipfrac", "explained_variance", "learning_rate"):
            self.assertEqual(len(history[key]), 2, key)
        self.assertTrue(all(x >= -1e-7 for x in history["approx_kl"]))
        self.assertTrue(all(0.0 <= x <= 1.0 for x in history["clipfrac"]))
        self.assertEqual(FIELDNAMES[-4:], ("approx_kl", "clipfrac", "explained_variance", "learning_rate"))
        self.assertEqual(FIELDNAMES[:3], ("timesteps", "fps", "policy_loss"))

    def test_explained_variance_definition(self):
        y = torch.tensor([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(explained_variance(y, y), 1.0)
        self.assertAlmostEqual(explained_variance(torch.zeros(4), y), 0.0)
        self.assertIsNone(explained_variance(y, torch.ones(4)))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestAsyncPPO(unittest.TestCase):
    def test_async_and_sync_train_to_identical_weights(self):
        factory = ConfigEnvFactory(TOY_CFG)

        def run(env):
            torch.manual_seed(0)
            with tempfile.TemporaryDirectory() as tmp:
                trainer = PPOTrainer(env, ActorCritic(num_actions=3), PPOConfig(
                    rollout_length=16, minibatch_size=8, update_epochs=1, total_timesteps=96,
                    num_envs=3, checkpoint_dir=tmp, checkpoint_every_updates=0))
                with contextlib.redirect_stdout(io.StringIO()):
                    history = trainer.train()
            env.close()
            return trainer, history

        sync, hs = run(SyncVectorEnv([factory] * 3))
        avec, ha = run(AsyncVectorEnv([factory] * 3))
        self.assertGreater(sum(hs["upd_episodes"]), 0)
        self.assertEqual(hs["upd_episodes"], ha["upd_episodes"])
        self.assertEqual(hs["policy_loss"], ha["policy_loss"])
        for p, q in zip(sync.model.parameters(), avec.model.parameters(), strict=True):
            self.assertTrue(torch.equal(p, q))


if __name__ == "__main__":
    unittest.main()
