"""Tests for PPO (requires torch; uses the toy game only)."""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.ppo import PPOConfig, PPOTrainer, compute_gae

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _tiny_config(**overrides):
    args = {
        "rollout_length": 32,
        "minibatch_size": 16,
        "update_epochs": 1,
        "total_timesteps": 64,
        "checkpoint_every_updates": 100,
    }
    args.update(overrides)
    return PPOConfig(**args)


def _trainer(**overrides):
    env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
    model = ActorCritic(num_actions=int(env.action_space.n))
    return PPOTrainer(env, model, _tiny_config(**overrides))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestGAE(unittest.TestCase):
    def test_hand_computed_returns(self):
        rewards = torch.tensor([1.0, 1.0, 1.0])
        values = torch.tensor([0.0, 0.0, 0.0])
        adv, ret = compute_gae(rewards, values, torch.zeros(3), torch.tensor(0.0), gamma=1.0, gae_lambda=1.0)
        self.assertTrue(torch.allclose(adv, torch.tensor([3.0, 2.0, 1.0])))
        self.assertTrue(torch.allclose(ret, torch.tensor([3.0, 2.0, 1.0])))

    def test_terminal_masks_bootstrap(self):
        rewards = torch.tensor([1.0, 5.0])
        values = torch.tensor([0.0, 0.0])
        adv, _ = compute_gae(rewards, values, torch.tensor([1.0, 0.0]), torch.tensor(9.0), gamma=1.0, gae_lambda=1.0)
        self.assertAlmostEqual(adv[0].item(), 1.0)  # next_value ignored across termination
        self.assertAlmostEqual(adv[1].item(), 14.0)  # 5 + bootstrap 9

    def test_discounted(self):
        rewards = torch.tensor([0.0, 1.0])
        values = torch.tensor([0.0, 0.0])
        adv, _ = compute_gae(rewards, values, torch.zeros(2), torch.tensor(0.0), gamma=0.5, gae_lambda=1.0)
        self.assertAlmostEqual(adv[0].item(), 0.5)
        self.assertAlmostEqual(adv[1].item(), 1.0)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestPPOTrainer(unittest.TestCase):
    def test_rollout_shapes(self):
        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        self.assertEqual(tuple(buf["obs"].shape), (32, 4, 84, 84))
        self.assertEqual(tuple(buf["actions"].shape), (32,))
        self.assertEqual(tuple(buf["logprobs"].shape), (32,))
        self.assertTrue(set(buf["actions"].tolist()) <= {0, 1, 2})

    def test_update_runs_and_changes_params(self):
        trainer = _trainer()
        before = [p.clone() for p in trainer.model.parameters()]
        buf, *_ = trainer.collect_rollout()
        stats = trainer.update(buf)
        for key in ("policy_loss", "value_loss", "entropy", "timesteps"):
            self.assertIn(key, stats)
            self.assertTrue(abs(stats[key]) != float("inf"))
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(before, trainer.model.parameters(), strict=False)))

    def test_checkpoint_roundtrip_and_resume(self):
        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        trainer.update(buf)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ckpt.pt")
            trainer.save_checkpoint(path)
            self.assertTrue(Path(path).exists())
            env2 = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            resumed = PPOTrainer.load_checkpoint(path, env2)
        self.assertEqual(resumed.num_timesteps, trainer.num_timesteps)
        for a, b in zip(trainer.model.parameters(), resumed.model.parameters(), strict=False):
            self.assertTrue(torch.equal(a, b))
        buf2, *_ = resumed.collect_rollout()  # training continues after restore
        resumed.update(buf2)
        self.assertGreater(resumed.num_timesteps, trainer.num_timesteps)

    def test_short_training_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            model = ActorCritic(num_actions=int(env.action_space.n))
            config = _tiny_config(total_timesteps=128, checkpoint_dir=tmp)
            history = PPOTrainer(env, model, config).train()
            for key in ("timesteps", "fps", "policy_loss", "value_loss", "entropy", "mean_reward", "episodes"):
                self.assertIn(key, history)
                self.assertTrue(len(history[key]) > 0)
            self.assertEqual(history["timesteps"][-1], 128)
            self.assertTrue((Path(tmp) / "ppo_final.pt").exists())
            self.assertGreaterEqual(history["episodes"][-1], 1)  # short env => episodes finish

    def test_training_stops_at_non_divisible_timestep_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            model = ActorCritic(num_actions=int(env.action_space.n))
            config = _tiny_config(
                rollout_length=4, minibatch_size=2, total_timesteps=10,
                checkpoint_dir=tmp,
            )
            trainer = PPOTrainer(env, model, config)

            history = trainer.train()

            self.assertEqual(trainer.num_timesteps, 10)
            self.assertEqual(history["timesteps"], [4, 8, 10])

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            PPOConfig(learning_rate=-1.0)
        with self.assertRaises(ValueError):
            PPOConfig(rollout_length=0)
        with self.assertRaises(ValueError):
            PPOConfig(gamma=1.5)
        with self.assertWarnsRegex(UserWarning, "unknown_key"):
            cfg = PPOConfig.from_dict({"learning_rate": 1e-3, "unknown_key": 1})
        self.assertAlmostEqual(cfg.learning_rate, 1e-3)
        with self.assertRaises(ValueError):
            PPOConfig(seed=-1)
        with self.assertRaises(ValueError):
            PPOConfig.from_dict({"seed": -1})
        for bad in (-1, -100, 0.5, "10", True):
            with self.assertRaises(ValueError, msg=f"interval={bad!r}"):
                PPOConfig(checkpoint_every_updates=bad)
        self.assertEqual(PPOConfig(checkpoint_every_updates=0).checkpoint_every_updates, 0)

    def test_disabled_periodic_checkpoints(self):
        # interval 0: only the final checkpoint is written, no ZeroDivisionError.
        with tempfile.TemporaryDirectory() as tmp:
            env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
            model = ActorCritic(num_actions=int(env.action_space.n))
            config = _tiny_config(total_timesteps=64, checkpoint_dir=tmp,
                                  checkpoint_every_updates=0)
            PPOTrainer(env, model, config).train()
            files = sorted(Path(tmp).glob("*.pt"))
            self.assertEqual([p.name for p in files], ["ppo_final.pt"])

    def test_save_replaces_existing_checkpoint_atomically(self):
        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        trainer.update(buf)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ckpt.pt")
            trainer.save_checkpoint(path)
            first = Path(path).read_bytes()
            buf2, *_ = trainer.collect_rollout()
            trainer.update(buf2)
            trainer.save_checkpoint(path)  # must replace, not fail or duplicate
            self.assertTrue(Path(path).exists())
            self.assertEqual(len(list(Path(tmp).glob("*.pt"))), 1)
            self.assertNotEqual(Path(path).read_bytes(), first)
            # No stray temp files left behind on the happy path.
            self.assertEqual(list(Path(tmp).glob(".tmp-ckpt-*")), [])
            # Replaced file still loads and resumes.
            resumed = PPOTrainer.load_checkpoint(
                path, PreprocessingWrapper(ToyPongEnv(max_steps=64)))
            self.assertEqual(resumed.num_timesteps, trainer.num_timesteps)

    def test_failed_save_preserves_existing_checkpoint(self):
        import torch as _torch

        trainer = _trainer()
        buf, *_ = trainer.collect_rollout()
        trainer.update(buf)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ckpt.pt")
            trainer.save_checkpoint(path)
            good = Path(path).read_bytes()
            orig_save = _torch.save

            def boom(*args, **kwargs):
                raise RuntimeError("simulated interrupted write")

            _torch.save = boom
            try:
                with self.assertRaises(RuntimeError):
                    trainer.save_checkpoint(path)
            finally:
                _torch.save = orig_save
            # Old checkpoint intact; no stray temp file.
            self.assertEqual(Path(path).read_bytes(), good)
            self.assertEqual(list(Path(tmp).glob(".tmp-ckpt-*")), [])
            resumed = PPOTrainer.load_checkpoint(
                path, PreprocessingWrapper(ToyPongEnv(max_steps=64)))
            self.assertEqual(resumed.num_timesteps, trainer.num_timesteps)


class TestRecurrentFixes(unittest.TestCase):
    def _trainer(self, max_steps, rollout_length, minibatch_size=4):
        env = PreprocessingWrapper(ToyPongEnv(max_steps=max_steps))
        model = ActorCritic(num_actions=int(env.action_space.n))
        config = PPOConfig(rollout_length=rollout_length, minibatch_size=minibatch_size,
                           update_epochs=1, total_timesteps=rollout_length, checkpoint_every_updates=100)
        return PPOTrainer(env, model, config)

    def test_hidden_reset_at_mid_rollout_done(self):
        torch.manual_seed(0)
        trainer = self._trainer(max_steps=2, rollout_length=5, minibatch_size=5)
        trainer.collect_rollout()  # warms carry so rollout 2 starts from nonzero hidden
        buf, *_ = trainer.collect_rollout()
        h0 = buf["h0"]
        self.assertFalse(torch.allclose(h0, torch.zeros_like(h0)))  # setup: nonzero carry
        seen = []
        orig = trainer.model.forward_sequence

        def spy(obs_seq, hidden):
            seen.append(hidden.clone())
            return orig(obs_seq, hidden)

        trainer.model.forward_sequence = spy
        trainer.update(buf)
        # dones at t=1,3 -> segments [0:2] from h0, [2:4] and [4:5] from zeros
        self.assertEqual(len(seen), 3)
        self.assertTrue(torch.equal(seen[0].cpu(), h0.cpu()))
        self.assertTrue(torch.all(seen[1] == 0))
        self.assertTrue(torch.all(seen[2] == 0))

    def test_truncation_bootstrap_uses_pre_reset_obs(self):
        torch.manual_seed(1)
        trainer = self._trainer(max_steps=3, rollout_length=3, minibatch_size=3)
        calls = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            calls.append((obs.clone(), hidden.clone()))
            return orig(obs, hidden)

        trainer.model.forward = spy
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool(buf["dones"][-1]))  # setup: final step ends the episode
        self.assertFalse(bool(buf["terminated"][-1]))  # ...by truncation, not termination
        boot_obs, _ = calls[-1]  # last forward call is the bootstrap
        self.assertTrue(torch.equal(boot_obs.squeeze(0).cpu(), buf["next_obs"][-1]))

    def test_rollout_length_one_no_nan(self):
        env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
        model = ActorCritic(num_actions=int(env.action_space.n))
        config = PPOConfig(rollout_length=1, minibatch_size=1, update_epochs=1,
                           total_timesteps=2, checkpoint_every_updates=100)
        trainer = PPOTrainer(env, model, config)
        buf, *_ = trainer.collect_rollout()
        stats = trainer.update(buf)
        for key in ("policy_loss", "value_loss", "entropy"):
            self.assertTrue(abs(stats[key]) != float("inf") and stats[key] == stats[key], key)

    def test_trainer_keeps_train_mode(self):
        trainer = self._trainer(max_steps=64, rollout_length=8, minibatch_size=8)
        trainer.model.train()
        buf, *_ = trainer.collect_rollout()
        trainer.update(buf)
        self.assertTrue(trainer.model.training)


class ScriptedEnv:
    """Deterministic fake env: scripted (reward, terminated, truncated) per step.
    Frames carry the global step index (reset frames carry RESET_VAL), so tests
    can tell pre-reset, post-reset, and carried observations apart. No display,
    no randomness, no game logic.
    """

    RESET_VAL = -1.0

    def __init__(self, script, obs_shape=(1, 84, 84)):
        self.script = list(script)
        self.shape = obs_shape
        self.t = 0
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, seed=None, options=None):
        return np.full(self.shape, self.RESET_VAL, dtype=np.float32), {}

    def step(self, action):
        if self.t < len(self.script):
            reward, terminated, truncated = self.script[self.t]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.t += 1
        return (
            np.full(self.shape, float(self.t), dtype=np.float32),
            float(reward),
            bool(terminated),
            bool(truncated),
            {},
        )

    def close(self):
        pass

@unittest.skipUnless(_HAS_TORCH, "torch not installed")


class TestBoundarySemantics(unittest.TestCase):
    def _trainer(self, script, rollout_length, minibatch_size=None, seed=0):
        import numpy as np  # local import keeps module import light

        torch.manual_seed(seed)
        env = ScriptedEnv(script)
        model = ActorCritic(num_actions=2, in_channels=1, feature_dim=16, hidden_size=8)
        config = PPOConfig(rollout_length=rollout_length,
                           minibatch_size=minibatch_size or rollout_length,
                           update_epochs=1, total_timesteps=rollout_length,
                           checkpoint_every_updates=100)
        trainer = PPOTrainer(env, model, config)
        trainer._np = np
        return trainer

    def test_carry_across_clean_rollout_boundary(self):
        trainer = self._trainer([(0.0, False, False)] * 8, rollout_length=4)
        buf1, *_ = trainer.collect_rollout()
        self.assertFalse(bool(buf1["dones"].any()))
        carry = trainer._carry_hidden.clone()
        self.assertFalse(torch.allclose(carry, torch.zeros_like(carry)))
        buf2, *_ = trainer.collect_rollout()
        self.assertTrue(torch.equal(buf2["h0"].cpu(), carry.cpu()))

    def test_replay_matches_rollout_boundaries(self):
        script = [(0.0, False, False)] * 5 + [
            (0.0, False, False), (1.0, True, False), (0.0, False, False),
            (0.0, False, True), (0.0, False, False),
        ]
        trainer = self._trainer(script, rollout_length=5)
        trainer.collect_rollout()  # warm carry so h0 below is nonzero
        buf, *_ = trainer.collect_rollout()
        self.assertEqual([bool(d) for d in buf["dones"]], [False, True, False, True, False])
        h0 = buf["h0"]
        self.assertFalse(torch.allclose(h0, torch.zeros_like(h0)))
        seen = []
        orig = trainer.model.forward_sequence

        def spy(obs_seq, hidden):
            seen.append(hidden.clone())
            return orig(obs_seq, hidden)

        trainer.model.forward_sequence = spy
        trainer.update(buf)
        self.assertEqual(len(seen), 3)  # [0:2] from h0, [2:4] + [4:5] from zeros
        self.assertTrue(torch.equal(seen[0].cpu(), h0.cpu()))
        self.assertTrue(bool((seen[1] == 0).all()))
        self.assertTrue(bool((seen[2] == 0).all()))

    def test_reset_after_true_termination(self):
        script = [(0.0, False, False), (0.0, True, False)] + [(0.0, False, False)] * 4
        trainer = self._trainer(script, rollout_length=4)
        calls = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            calls.append((obs.clone(), hidden.clone()))
            return orig(obs, hidden)

        trainer.model.forward = spy
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool(buf["terminated"][1]))
        step2_obs, step2_hidden = calls[2]  # first step of the fresh episode
        self.assertTrue(bool((step2_obs == ScriptedEnv.RESET_VAL).all()))
        self.assertTrue(bool((step2_hidden == 0).all()))

    def test_truncation_bootstrap_obs_and_hidden(self):
        script = [(0.0, False, False), (0.0, False, True)] + [(0.0, False, False)] * 4
        trainer = self._trainer(script, rollout_length=2)
        calls = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            out = orig(obs, hidden)
            calls.append((obs.clone(), hidden.clone(), out[2].clone()))
            return out

        trainer.model.forward = spy
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool(buf["truncated"][-1]))
        self.assertFalse(bool(buf["terminated"][-1]))
        boot_obs, boot_hidden, _ = calls[-1]  # bootstrap call
        self.assertTrue(torch.equal(boot_obs.squeeze(0).cpu(), buf["next_obs"][-1]))
        self.assertFalse(bool((boot_obs == ScriptedEnv.RESET_VAL).all()))  # pre-reset, not fresh
        _, _, step1_out_hidden = calls[1]
        self.assertTrue(torch.equal(boot_hidden.cpu(), step1_out_hidden.cpu()))

    def test_truncation_then_reset(self):
        script = [(0.0, False, False), (0.0, False, True)] + [(0.0, False, False)] * 4
        trainer = self._trainer(script, rollout_length=3)
        calls = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            calls.append((obs.clone(), hidden.clone()))
            return orig(obs, hidden)

        trainer.model.forward = spy
        buf, *_ = trainer.collect_rollout()
        step2_obs, step2_hidden = calls[2]
        self.assertTrue(bool((step2_obs == ScriptedEnv.RESET_VAL).all()))
        self.assertTrue(bool((step2_hidden == 0).all()))

    def test_multiple_episodes_one_rollout(self):
        script = [(0.0, False, False), (1.0, True, False), (0.0, False, False),
                  (1.0, True, False)] + [(0.0, False, False)] * 6
        trainer = self._trainer(script, rollout_length=5)
        _, rewards, lengths, ext, intr, _ = trainer.collect_rollout()
        self.assertEqual(lengths, [2, 2])
        self.assertEqual(rewards, [1.0, 1.0])
        self.assertEqual(ext, [1.0, 1.0])

    def test_rollout_ends_exactly_at_boundary(self):
        script = [(0.0, False, False), (0.0, False, False), (1.0, True, False)]
        trainer = self._trainer(script, rollout_length=3)
        buf, rewards, lengths, _, _, _ = trainer.collect_rollout()
        self.assertEqual(lengths, [3])
        self.assertTrue(bool((trainer._carry_hidden == 0).all()))
        self.assertTrue(bool((trainer._carry_obs == ScriptedEnv.RESET_VAL).all()))
        buf2, *_ = trainer.collect_rollout()
        self.assertTrue(bool((buf2["h0"] == 0).all()))
        self.assertTrue(torch.equal(buf["next_value"], torch.zeros(())))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMidRolloutTruncationBootstrap(unittest.TestCase):
    """A time-limit cutoff inside a rollout must bootstrap from its own
    final pre-reset observation, not from the fresh episode that follows it.
    """

    # t=1 truncates; the rollout then collects the new episode at t=2..3.
    SCRIPT = [(0.0, False, False), (1.0, False, True),
              (0.0, False, False), (0.0, False, False)]

    def _trainer(self, script, rollout_length=4):
        torch.manual_seed(0)
        env = ScriptedEnv(script)
        model = ActorCritic(num_actions=2, in_channels=1, feature_dim=16, hidden_size=8)
        config = PPOConfig(rollout_length=rollout_length, minibatch_size=rollout_length,
                           update_epochs=1, total_timesteps=rollout_length,
                           checkpoint_every_updates=100)
        return PPOTrainer(env, model, config)

    @staticmethod
    def _gae(rewards, values, terminated, next_values, gamma, lam):
        """Hand-computed GAE for an explicit next-observation value per step."""
        adv = torch.zeros_like(rewards)
        last = 0.0
        for t in reversed(range(len(rewards))):
            nonterminal = 1.0 - float(terminated[t])
            delta = (float(rewards[t]) + gamma * float(next_values[t]) * nonterminal
                     - float(values[t]))
            last = delta + gamma * lam * nonterminal * last
            adv[t] = last
        return adv, adv + values

    @staticmethod
    def _captured_gae(trainer, buf):
        import training.ppo as ppo_mod

        captured = {}
        original = ppo_mod.compute_gae

        def spy(rewards, values, terminated, next_value, gamma, lam, next_values=None):
            adv, ret = original(rewards, values, terminated, next_value, gamma, lam,
                                next_values)
            captured["adv"], captured["ret"] = adv.clone(), ret.clone()
            return adv, ret

        ppo_mod.compute_gae = spy
        try:
            trainer.update(buf)
        finally:
            ppo_mod.compute_gae = original
        return captured

    def test_mid_rollout_truncation_uses_pre_reset_bootstrap(self):
        trainer = self._trainer(self.SCRIPT)
        calls, original = [], trainer.model.forward

        def spy(obs, hidden):
            logits, value, h_out = original(obs, hidden)
            calls.append((obs.clone(), h_out.clone()))
            return logits, value, h_out

        trainer.model.forward = spy
        buf, *_ = trainer.collect_rollout()
        trainer.model.forward = original

        self.assertEqual([bool(x) for x in buf["truncated"]], [False, True, False, False])
        self.assertFalse(bool(buf["terminated"][1]))
        with torch.no_grad():
            pre_reset = original(buf["next_obs"][1].unsqueeze(0), calls[1][1])[1].squeeze(0)
        post_reset = buf["values"][2]  # first value of the fresh episode
        self.assertNotAlmostEqual(float(pre_reset), float(post_reset), places=4)
        self.assertAlmostEqual(float(buf["next_values"][1]), float(pre_reset), places=5)

        values, rewards = buf["values"].float(), buf["rewards"].float()
        terminated = buf["terminated"].float()
        gamma, lam = trainer.config.gamma, trainer.config.gae_lambda
        correct = [values[1], pre_reset, values[3], buf["next_value"]]
        biased = [values[1], post_reset, values[3], buf["next_value"]]
        expected_adv, expected_ret = self._gae(rewards, values, terminated, correct, gamma, lam)
        biased_adv, _ = self._gae(rewards, values, terminated, biased, gamma, lam)
        self.assertFalse(torch.allclose(biased_adv, expected_adv, atol=1e-5))

        captured = self._captured_gae(trainer, buf)
        self.assertTrue(torch.allclose(captured["adv"], expected_adv, atol=1e-5))
        self.assertTrue(torch.allclose(captured["ret"], expected_ret, atol=1e-5))

    def test_terminated_boundary_still_bootstraps_zero(self):
        trainer = self._trainer([(0.0, False, False), (1.0, True, False),
                                 (0.0, False, False), (0.0, False, False)])
        buf, *_ = trainer.collect_rollout()
        self.assertTrue(bool(buf["terminated"][1]))
        self.assertFalse(bool(buf["truncated"][1]))

        values, rewards = buf["values"].float(), buf["rewards"].float()
        terminated = buf["terminated"].float()
        gamma, lam = trainer.config.gamma, trainer.config.gae_lambda
        zero = [values[1], torch.zeros(()), values[3], buf["next_value"]]
        perturbed = [values[1], values[2] + 100.0, values[3], buf["next_value"]]
        adv_zero, _ = self._gae(rewards, values, terminated, zero, gamma, lam)
        adv_perturbed, _ = self._gae(rewards, values, terminated, perturbed, gamma, lam)
        # The terminal mask makes the following value irrelevant.
        self.assertTrue(torch.allclose(adv_zero, adv_perturbed, atol=1e-6))
        self.assertAlmostEqual(float(adv_zero[1]), float(rewards[1] - values[1]), places=5)

        captured = self._captured_gae(trainer, buf)
        self.assertTrue(torch.allclose(captured["adv"], adv_zero, atol=1e-5))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMetricsEdgeCases(unittest.TestCase):
    def test_pixel_change_single_frame_is_zero_not_nan(self):
        env = PreprocessingWrapper(ToyPongEnv(max_steps=64))
        model = ActorCritic(num_actions=int(env.action_space.n))
        config = PPOConfig(rollout_length=1, minibatch_size=1, update_epochs=1,
                           total_timesteps=1, checkpoint_every_updates=100)
        buf, *_ = PPOTrainer(env, model, config).collect_rollout()
        self.assertEqual(buf["pixel_change"], 0.0)
        self.assertTrue(buf["pixel_change"] == buf["pixel_change"])  # not NaN


if __name__ == "__main__":
    unittest.main()
