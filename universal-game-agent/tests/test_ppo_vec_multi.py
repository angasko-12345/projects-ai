"""Multi-env PPO: slot association, hidden isolation, bootstrap, GAE.

Scripted per-slot envs with deliberately different episode lengths and
slot-tagged observations, so any cross-slot mixing is obvious. No display,
no window. Torch-gated like the other PPO tests.
"""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import torch

    from agent.model import ActorCritic
    from environment.vec import SyncVectorEnv
    from training import ppo as ppo_mod
    from training.ppo import PPOConfig, PPOTrainer

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

OBS_SHAPE = (4, 84, 84)


class SlotEnv:
    """Per-slot scripted env. Reset frames are -10-slot, step frames are
    100*step_counter+slot: every (time, slot) cell is identifiable."""

    def __init__(self, script, slot):
        self.script = list(script)
        self.slot = slot
        self.t = 0
        self.resets = 0
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, *, seed=None, options=None):
        self.resets += 1
        self.t = 0
        return np.full(OBS_SHAPE, -10.0 - self.slot, dtype=np.float32), {}

    def step(self, action):
        if self.t < len(self.script):
            reward, terminated, truncated = self.script[self.t]
        else:
            reward, terminated, truncated = 0.0, False, False
        self.t += 1
        obs = np.full(OBS_SHAPE, 100.0 * self.t + self.slot, dtype=np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

    def close(self):
        pass


def _trainer(envs, rollout_length, seed=0, minibatch=None):
    torch.manual_seed(seed)
    vec = SyncVectorEnv([lambda env=env: env for env in envs])
    model = ActorCritic(num_actions=2)
    config = PPOConfig(rollout_length=rollout_length,
                       minibatch_size=minibatch or rollout_length,
                       update_epochs=1, total_timesteps=10 ** 9,
                       learning_rate=1e-3, checkpoint_dir="/tmp/never",
                       checkpoint_every_updates=10 ** 9)
    return PPOTrainer(vec, model, config)


def _rollout(trainer, seed=999):
    torch.manual_seed(seed)
    return trainer.collect_rollout()


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvRollout(unittest.TestCase):
    def _three(self):
        slot0 = SlotEnv([(0.0, False, False), (1.0, True, False)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 4 + [(2.0, True, False)], 1)
        slot2 = SlotEnv([(0.0, False, False)] * 2 + [(0.5, False, True)], 2)
        return slot0, slot1, slot2

    def test_slots_stay_associated(self):
        slot0, slot1, slot2 = self._three()
        trainer = _trainer([slot0, slot1, slot2], 6)
        buf, ep_rewards, ep_lengths, ep_ext, ep_int, ep_term = _rollout(trainer)
        self.assertEqual(buf["terminated"][:, 0].tolist(),
                         [False, True, False, True, False, True])
        self.assertEqual(buf["terminated"][:, 1].tolist(),
                         [False] * 4 + [True, False])
        self.assertEqual(buf["truncated"][:, 2].tolist(),
                         [False, False, True, False, False, True])
        self.assertEqual(buf["ext"][:, 0].tolist(),
                         [0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
        self.assertEqual(buf["ext"][:, 1].tolist(),
                         [0.0, 0.0, 0.0, 0.0, 2.0, 0.0])
        self.assertEqual(buf["ext"][:, 2].tolist(),
                         [0.0, 0.0, 0.5, 0.0, 0.0, 0.5])
        # Reset counts follow only each slot's own boundaries.
        self.assertEqual(slot0.resets, 1 + 3)
        self.assertEqual(slot1.resets, 1 + 1)
        self.assertEqual(slot2.resets, 1 + 2)
        # Post-boundary observations are that slot's own reset frames.
        self.assertTrue((buf["obs"][2, 0] == -10.0).all())
        self.assertTrue((buf["obs"][5, 1] == -11.0).all())
        self.assertTrue((buf["obs"][3, 2] == -12.0).all())

    def test_selective_reset_only_finished_slots(self):
        slot0, slot1, slot2 = self._three()
        trainer = _trainer([slot0, slot1, slot2], 6)
        calls = []
        orig = trainer.env.reset_env

        def spy(i, *a, **k):
            calls.append(i)
            return orig(i, *a, **k)

        trainer.env.reset_env = spy
        try:
            _rollout(trainer)
        finally:
            trainer.env.reset_env = orig
        self.assertEqual(sorted(calls), [0, 0, 0, 1, 2, 2])

    def test_determinism(self):
        def run():
            trainer = _trainer(list(self._three()), 6)
            return _rollout(trainer)
        buf1, *rest1 = run()
        buf2, *rest2 = run()
        for key in ("obs", "actions", "logprobs", "values", "ext", "terminated",
                    "truncated", "dones", "next_obs", "rewards", "int_rewards"):
            self.assertTrue(torch.equal(buf1[key], buf2[key]), key)
        self.assertEqual(rest1, rest2)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvHiddenIsolation(unittest.TestCase):
    def test_hidden_slices_are_independent(self):
        script1 = [(0.0, False, False)] * 6  # slot 1 never ends
        torch.manual_seed(0)
        solo_env = SlotEnv(script1, 1)
        solo = PPOTrainer(solo_env, ActorCritic(num_actions=2),
                          PPOConfig(rollout_length=4, minibatch_size=4, update_epochs=1,
                                    total_timesteps=4, learning_rate=1e-3,
                                    checkpoint_dir="/tmp/never", checkpoint_every_updates=100))
        torch.manual_seed(0)
        slot0 = SlotEnv([(0.0, False, False), (1.0, True, False)], 0)
        slot1 = SlotEnv(script1, 1)
        model = ActorCritic(num_actions=2)
        vec = SyncVectorEnv([lambda: slot0, lambda: slot1])
        trainer = PPOTrainer(vec, model, PPOConfig(
            rollout_length=4, minibatch_size=4, update_epochs=1, total_timesteps=4,
            learning_rate=1e-3, checkpoint_dir="/tmp/never", checkpoint_every_updates=100))
        solo_seen, vec_seen = [], []
        solo_orig, vec_orig = solo.model.forward, model.forward
        solo.model.forward = lambda obs, h, _o=solo_orig: (solo_seen.append(h.detach().cpu().clone()), _o(obs, h))[1]
        model.forward = lambda obs, h, _o=vec_orig: (vec_seen.append(h.detach().cpu().clone()), _o(obs, h))[1]
        torch.manual_seed(999)
        solo.collect_rollout()
        torch.manual_seed(999)
        trainer.collect_rollout()
        # Slot 1 must see exactly what the solo run saw: slot 0's reset left
        # it untouched. The batched GRU (N=2) vs lone GRU (N=1) differ only at
        # float32 rounding (~1.6e-5); assert with tolerance. The exact-zero
        # invariants below pin down the structural isolation regardless.
        for t in range(4):
            self.assertTrue(torch.allclose(vec_seen[t][:, 1, :], solo_seen[t][:, 0, :],
                                        rtol=1e-4, atol=1e-5), t)
        # Slot 0's hidden input is zeros exactly after its done (t=1 -> input at t=2).
        self.assertTrue((vec_seen[2][:, 0, :] == 0).all())
        self.assertFalse((vec_seen[1][:, 0, :] == 0).all())
        self.assertFalse((vec_seen[1][:, 1, :] == 0).all())


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvBootstrap(unittest.TestCase):
    def test_terminated_final_step_bootstraps_zero(self):
        slot0 = SlotEnv([(0.0, False, False)] * 3 + [(1.0, True, False)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 4, 1)
        trainer = _trainer([slot0, slot1], 4)
        buf, *_ = _rollout(trainer)
        self.assertTrue(bool(buf["terminated"][-1, 0]))
        self.assertEqual(float(buf["next_value"][0]), 0.0)
        self.assertFalse(bool(buf["terminated"][-1, 1] or buf["truncated"][-1, 1]))

    def test_truncated_final_step_bootstraps_pre_reset(self):
        slot0 = SlotEnv([(0.0, False, False)] * 3 + [(0.5, False, True)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 4, 1)
        trainer = _trainer([slot0, slot1], 4)
        post = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            out = orig(obs, hidden)
            post.append(out[2].detach().cpu().clone())
            return out

        trainer.model.forward = spy
        buf, *_ = _rollout(trainer)
        self.assertTrue(bool(buf["truncated"][-1, 0]))
        # post[:4] are the rollout steps' post-step hiddens; later entries
        # are the bootstrap calls themselves. post[3] is pre-reset state.
        pre = post[3]
        with torch.no_grad():
            expect0 = orig(
                buf["next_obs"][-1, 0].unsqueeze(0), pre[:, 0, :].unsqueeze(1))[1]
            expect1 = orig(
                buf["next_obs"][-1, 1].unsqueeze(0), pre[:, 1, :].unsqueeze(1))[1]
        self.assertAlmostEqual(float(buf["next_value"][0]), float(expect0), places=5)
        # Slot 1 ran on: bootstrap from the carried (not reset) state.
        self.assertAlmostEqual(float(buf["next_value"][1]), float(expect1), places=5)
        # And slot 0 provably did not use its reset frame (-10.0).
        with torch.no_grad():
            reset_based = orig(
                torch.full_like(buf["next_obs"][-1, 0].unsqueeze(0), -10.0),
                pre[:, 0, :].unsqueeze(1))[1]
        self.assertNotAlmostEqual(float(buf["next_value"][0]), float(reset_based), places=5)

    def test_mid_rollout_truncation_bootstraps_pre_reset(self):
        # Slot 0 truncates at t=1, then runs a fresh episode through t=3.
        slot0 = SlotEnv([(0.0, False, False), (1.0, False, True),
                         (0.0, False, False), (0.0, False, False)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 4, 1)
        trainer = _trainer([slot0, slot1], 4)
        calls = []
        orig = trainer.model.forward

        def spy(obs, hidden):
            logits, value, h_out = orig(obs, hidden)
            calls.append((obs.clone(), h_out.clone()))
            return logits, value, h_out

        trainer.model.forward = spy
        buf, *_ = _rollout(trainer)
        trainer.model.forward = orig
        self.assertTrue(bool(buf["truncated"][1, 0]))
        with torch.no_grad():
            pre = orig(buf["next_obs"][1, 0].unsqueeze(0),
                       calls[1][1][:, 0, :].unsqueeze(1))[1]
        post = float(buf["values"][2, 0])  # the fresh episode's first value
        self.assertNotAlmostEqual(float(pre), post, places=5)
        self.assertAlmostEqual(float(buf["next_values"][1, 0]), float(pre), places=5)
        # Slot 1 never truncated: its chain is the plain values[t + 1].
        self.assertAlmostEqual(float(buf["next_values"][1, 1]),
                               float(buf["values"][2, 1]), places=5)

        seen = []
        orig_gae = ppo_mod.compute_gae

        def spy_gae(rewards, values, terminated, next_value, gamma, lam, next_values=None):
            seen.append((values.clone(), None if next_values is None else next_values.clone()))
            return orig_gae(rewards, values, terminated, next_value, gamma, lam, next_values)

        ppo_mod.compute_gae = spy_gae
        try:
            trainer.update(buf)
        finally:
            ppo_mod.compute_gae = orig_gae
        self.assertEqual(len(seen), 2)
        self.assertAlmostEqual(float(seen[0][1][1]), float(pre), places=5)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvGAE(unittest.TestCase):
    def test_per_slot_gae_matches_hand_computation(self):
        # slot0 terminates at t=1 and at the last step t=3; slot1 truncates
        # at t=1 and at t=3; slot2 never finishes, so its final step
        # bootstraps from the carried observation and hidden state.
        slot0 = SlotEnv([(0.0, False, False), (1.0, True, False)], 0)
        slot1 = SlotEnv([(0.0, False, False), (0.5, False, True)], 1)
        slot2 = SlotEnv([(0.0, False, False)] * 8, 2)
        trainer = _trainer([slot0, slot1, slot2], 4)
        calls, orig_forward = [], trainer.model.forward

        def spy_forward(obs, hidden):
            logits, value, h_out = orig_forward(obs, hidden)
            calls.append(h_out.clone())  # rollout zeroes finished slices in place
            return logits, value, h_out

        trainer.model.forward = spy_forward
        try:
            buf, *_ = _rollout(trainer)
        finally:
            trainer.model.forward = orig_forward
        T, N = buf["rewards"].shape
        self.assertEqual((T, N), (4, 3))
        self.assertEqual(buf["terminated"][:, 0].tolist(), [False, True, False, True])
        self.assertEqual(buf["truncated"][:, 1].tolist(), [False, True, False, True])
        self.assertFalse(bool(buf["dones"][:, 2].any()))

        def v(obs, h):
            with torch.no_grad():
                return float(orig_forward(obs.unsqueeze(0), h.unsqueeze(1))[1].reshape(()))

        # Expected bootstrap table, built without reading buf["next_values"]
        # or buf["next_value"].
        expected_nv = torch.zeros(T, N)
        for e in range(N):
            for t in range(T):
                if buf["terminated"][t, e]:
                    expected_nv[t, e] = 0.0
                elif buf["truncated"][t, e]:
                    # Own pre-reset frame with the post-step hidden state.
                    expected_nv[t, e] = v(buf["next_obs"][t, e], calls[t][:, e, :])
                elif t + 1 < T:
                    expected_nv[t, e] = float(buf["values"][t + 1, e])
                else:
                    carry = torch.from_numpy(np.ascontiguousarray(
                        trainer._carry_obs[e], dtype=np.float32))
                    expected_nv[t, e] = v(carry, trainer._carry_hidden[:, e, :])

        # The final row of the per-step table is the rollout bootstrap.
        self.assertTrue(torch.equal(buf["next_values"][-1], buf["next_value"]))
        for e in range(N):
            self.assertAlmostEqual(float(buf["next_value"][e]), float(expected_nv[-1, e]),
                                   places=5)
        self.assertNotEqual(float(expected_nv[-1, 2]), 0.0)
        # Non-terminal entries must match; terminated entries are masked.
        live = ~buf["terminated"]
        self.assertTrue(torch.allclose(buf["next_values"][live], expected_nv[live], atol=1e-5))

        seen = []
        orig_gae = ppo_mod.compute_gae

        def spy_gae(rewards, values, terminated, next_value, gamma, lam, next_values=None):
            adv, ret = orig_gae(rewards, values, terminated, next_value, gamma, lam,
                                next_values)
            seen.append(adv.clone())
            return adv, ret

        ppo_mod.compute_gae = spy_gae
        try:
            stats = trainer.update(buf)
        finally:
            ppo_mod.compute_gae = orig_gae
        self.assertEqual(len(seen), N)  # one GAE pass per slot, never mixed
        gamma, lam = trainer.config.gamma, trainer.config.gae_lambda
        for e, adv in enumerate(seen):
            exp_adv = [0.0] * T
            last = 0.0
            for t in reversed(range(T)):
                nonterm = 1.0 - float(buf["terminated"][t, e])
                delta = (float(buf["rewards"][t, e]) + gamma * float(expected_nv[t, e]) * nonterm
                         - float(buf["values"][t, e]))
                last = delta + gamma * lam * nonterm * last
                exp_adv[t] = last
            self.assertTrue(torch.allclose(
                adv, torch.tensor(exp_adv, dtype=adv.dtype), atol=1e-5), f"slot {e}")
        for key in ("policy_loss", "value_loss", "entropy"):
            self.assertTrue(abs(stats[key]) != float("inf"))
            self.assertEqual(stats[key], stats[key])  # no NaN

    def test_curiosity_boundary_masking_per_slot(self):
        from training.curiosity import CuriosityConfig, CuriosityModule

        slot0 = SlotEnv([(1.0, True, False), (0.0, False, False)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 4, 1)
        torch.manual_seed(0)
        vec = SyncVectorEnv([lambda: slot0, lambda: slot1])
        model = ActorCritic(num_actions=2)
        curiosity = CuriosityModule(num_actions=2, config=CuriosityConfig(), device="cpu")
        trainer = PPOTrainer(vec, model, PPOConfig(
            rollout_length=4, minibatch_size=4, update_epochs=1, total_timesteps=4,
            learning_rate=1e-3, checkpoint_dir="/tmp/never",
            checkpoint_every_updates=100), curiosity=curiosity)
        buf, *_ = _rollout(trainer)
        self.assertEqual(tuple(buf["int_rewards"].shape), (4, 2))
        masked = buf["int_rewards"][buf["dones"]]
        self.assertTrue(bool((masked == 0).all()))
        stats = trainer.update(buf)
        self.assertIn("predictor_loss", stats)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestMultiEnvTraining(unittest.TestCase):
    def test_tiny_two_env_train(self):
        slot0 = SlotEnv([(1.0, True, False), (0.0, False, False)], 0)
        slot1 = SlotEnv([(0.0, False, False)] * 5 + [(2.0, True, False)], 1)
        with tempfile.TemporaryDirectory() as tmp:
            torch.manual_seed(0)
            vec = SyncVectorEnv([lambda: slot0, lambda: slot1])
            trainer = PPOTrainer(vec, ActorCritic(num_actions=2), PPOConfig(
                rollout_length=6, minibatch_size=6, update_epochs=1,
                total_timesteps=24, learning_rate=1e-3,
                checkpoint_dir=str(Path(tmp) / "ckpt"), checkpoint_every_updates=100))
            torch.manual_seed(7)
            history = trainer.train()
            self.assertEqual(len(history["timesteps"]), 2)  # 12 steps/update (6 rollout x 2 envs)
            self.assertEqual(history["timesteps"], [12, 24])
            self.assertGreater(sum(history["upd_episodes"]), 2)
            final = Path(tmp) / "ckpt" / "ppo_final.pt"
            self.assertTrue(final.exists())
            # Factories, not instances -- SyncVectorEnv calls each make().
            resumed = PPOTrainer.load_checkpoint(str(final), SyncVectorEnv(
                [lambda: SlotEnv([(0.0, False, False)], 0),
                 lambda: SlotEnv([(0.0, False, False)], 1)]))
            self.assertEqual(resumed.num_timesteps, 24)
            # Slot 1's long episode was never cut by slot 0's boundaries:
            # it terminates once per 6-step rollout on its own schedule.
            self.assertEqual(slot1.resets, 3)  # initial + its own two done
            # ...and nobody is reset every step.
            self.assertLess(slot0.resets, trainer.num_timesteps)
            self.assertLess(slot1.resets, trainer.num_timesteps)


if __name__ == "__main__":
    unittest.main()
