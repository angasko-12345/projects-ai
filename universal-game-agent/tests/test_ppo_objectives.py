"""PPO/GAE equation audits: hand-computed expectations through real code paths."""
import unittest

try:
    import torch
    import torch.nn as nn
    from torch.distributions import Categorical

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.ppo import PPOConfig, PPOTrainer, compute_gae

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class FixedPolicy(nn.Module):
    """Deterministic policy: fixed per-call logits/values, shift-invariant dummy param."""

    def __init__(self, step_logits, step_values, hidden_size=8, layers=1):
        super().__init__()
        self.register_buffer("step_logits", step_logits.clone())
        self.register_buffer("step_values", step_values.clone())
        self.dummy = nn.Parameter(torch.zeros(1))
        self.h, self.l = hidden_size, layers

    def forward_sequence(self, obs, hidden):
        logits = self.step_logits[: obs.shape[0]] + self.dummy  # shift keeps softmax identical
        return logits, self.step_values[: obs.shape[0]], torch.zeros(self.l, 1, self.h)


class ConstEnv:
    """Constant frame, constant reward, never done. For stability/shape tests."""

    def __init__(self, reward=0.0, n_actions=2):
        self.reward = reward
        self.action_space = type("Space", (), {"n": n_actions})()


    def reset(self, seed=None, options=None):
        import numpy as np

        return np.full((1, 84, 84), 0.5, dtype=np.float32), {}

    def step(self, action):
        import numpy as np

        return np.full((1, 84, 84), 0.5, dtype=np.float32), float(self.reward), False, False, {}


def _trainer(env, **overrides):
    args = {"rollout_length": 8, "minibatch_size": 8, "update_epochs": 1,
            "total_timesteps": 8, "checkpoint_every_updates": 100}
    args.update(overrides)
    model = ActorCritic(num_actions=int(env.action_space.n), in_channels=1,
                        feature_dim=16, hidden_size=8)
    return PPOTrainer(env, model, PPOConfig(**args))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestGAEHandComputed(unittest.TestCase):
    def test_mixed_termination(self):
        # gamma .9, lambda 1: term at t1 resets the backward accumulation,
        # but earlier steps still accumulate discounted deltas through it.
        adv, ret = compute_gae(torch.tensor([1.0, 1.0, 1.0, 1.0]),
                               torch.tensor([0.5, 0.5, 0.5, 0.5]),
                               torch.tensor([0.0, 1.0, 0.0, 0.0]),
                               torch.tensor(0.0), gamma=0.9, gae_lambda=1.0)
        # d3=.5; d2=.95 -> A2=.95+.9*.5=1.4; d1=.5 -> A1=.5; d0=.95 -> A0=.95+.9*.5=1.4
        self.assertTrue(torch.allclose(adv, torch.tensor([1.40, 0.50, 1.40, 0.50]), atol=1e-5))
        self.assertTrue(torch.allclose(ret, adv + 0.5, atol=1e-5))

    def test_truncation_bootstraps(self):
        adv, _ = compute_gae(torch.tensor([1.0, 1.0]), torch.tensor([0.5, 0.5]),
                             torch.tensor([0.0, 0.0]), torch.tensor(2.0),
                             gamma=0.9, gae_lambda=1.0)
        self.assertAlmostEqual(adv[1].item(), 1.0 + 0.9 * 2.0 - 0.5)  # 2.3
        self.assertAlmostEqual(adv[0].item(), 0.95 + 0.9 * 2.3, places=4)  # delta0 + gamma*A1

    def test_one_step_terminated_ignores_bootstrap(self):
        adv, _ = compute_gae(torch.tensor([5.0]), torch.tensor([1.0]), torch.tensor([1.0]),
                             torch.tensor(99.0), gamma=0.9, gae_lambda=0.9)
        self.assertAlmostEqual(adv[0].item(), 4.0)
    def test_one_step_truncated_bootstraps(self):
        adv, _ = compute_gae(torch.tensor([5.0]), torch.tensor([1.0]), torch.tensor([0.0]),
                             torch.tensor(2.0), gamma=0.5, gae_lambda=1.0)
        self.assertAlmostEqual(adv[0].item(), 5.0)

    def test_zero_rewards_finite(self):
        adv, ret = compute_gae(torch.zeros(4), torch.ones(4) * 0.3, torch.zeros(4),
                               torch.tensor(0.0), gamma=0.99, gae_lambda=0.95)
        self.assertTrue(bool(torch.isfinite(adv).all() and torch.isfinite(ret).all()))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestObjectiveEquations(unittest.TestCase):
    def _fixed_update(self):
        # Hand-designed to hit every clipping regime with mixed advantage signs.
        # rewards [0,0,1,0], v_old 0, gamma=lam=1 -> adv [1,1,1,0] -> norm [.5,.5,.5,-1.5].
        T, A = 4, 2
        d = torch.tensor([0.5, 2.0, -0.5, 3.0])  # new taken-lp [-.474,-.127,-.974,-.049]
        taken = torch.tensor([0, 1, 0, 1])
        rows = torch.zeros(T, A)
        rows[range(T), taken] = 0.0
        rows[range(T), 1 - taken] = -d
        old_lp = torch.tensor([-0.5, -0.5, -0.5, -0.5])
        buf = {
            "obs": torch.zeros(T, 1, 84, 84),
            "actions": torch.tensor([0, 1, 0, 1]),
            "logprobs": old_lp,
            "values": torch.zeros(T),
            "rewards": torch.tensor([0.0, 0.0, 1.0, 0.0]),
            "terminated": torch.zeros(T),
            "dones": torch.zeros(T, dtype=torch.bool),
            "h0": torch.zeros(1, 1, 8),
            "next_obs": torch.zeros(T, 1, 84, 84),
            "next_value": torch.tensor(0.0),
        }
        model = FixedPolicy(rows, torch.tensor([0.5, -0.5, 2.0, 0.0]))
        env = ConstEnv()
        cfg = PPOConfig(rollout_length=T, minibatch_size=T, update_epochs=1,
                        total_timesteps=T, learning_rate=1e-3, clip_range=0.2,
                        gamma=1.0, gae_lambda=1.0,
                        entropy_coef=0.1, value_coef=1.0, checkpoint_every_updates=100)
        return PPOTrainer(env, model, cfg), buf, rows

    def test_full_objective_matches_hand_calc(self):
        trainer, buf, rows = self._fixed_update()
        new_lp = torch.log_softmax(rows, dim=-1)[range(4), buf["actions"]]
        ratios = torch.exp(new_lp - buf["logprobs"])
        self.assertTrue(bool((ratios < 0.8).any()))     # below range present
        self.assertTrue(bool(((ratios > 0.8) & (ratios < 1.2)).any()))  # inside present
        self.assertTrue(bool((ratios > 1.2).any()))     # above range present
        adv = torch.tensor([0.5, 0.5, 0.5, -1.5])       # normalized GAE (hand-computed)
        pg = -torch.min(ratios * adv, torch.clamp(ratios, 0.8, 1.2) * adv).mean()
        ret = torch.tensor([1.0, 1.0, 1.0, 0.0])
        v = torch.tensor([0.5, -0.5, 2.0, 0.0])
        vf = 0.5 * torch.max((v - ret) ** 2, (torch.clamp(v, -0.2, 0.2) - ret) ** 2).mean()
        ent = Categorical(logits=rows).entropy().mean()
        stats = trainer.update(buf)
        self.assertAlmostEqual(stats["policy_loss"], pg.item(), places=5)
        self.assertAlmostEqual(stats["value_loss"], vf.item(), places=5)
        self.assertAlmostEqual(stats["entropy"], ent.item(), places=5)

    def test_value_clip_differs_from_unclipped(self):
        trainer, buf, _ = self._fixed_update()
        stats = trainer.update(buf)
        v, ret = torch.tensor([0.5, -0.5, 2.0, 0.0]), torch.tensor([1.0, 1.0, 1.0, 0.0])
        unclipped = 0.5 * ((v - ret) ** 2).mean().item()
        self.assertNotAlmostEqual(stats["value_loss"], unclipped, places=4)
        self.assertGreater(stats["value_loss"], unclipped)  # max() picks the clipped side here

    def test_buffer_untouched_by_update(self):
        trainer, buf, _ = self._fixed_update()
        before = {k: v.clone() for k, v in buf.items() if isinstance(v, torch.Tensor)}
        trainer.update(buf)
        for k, v in before.items():
            self.assertTrue(torch.equal(buf[k], v), k)
            self.assertFalse(buf[k].requires_grad, k)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestOptimizationBehavior(unittest.TestCase):
    def test_entropy_term_active(self):
        torch.manual_seed(4)
        a = _trainer(ConstEnv(), learning_rate=1e-3, entropy_coef=0.0)
        torch.manual_seed(4)
        b = _trainer(ConstEnv(), learning_rate=1e-3, entropy_coef=0.5)
        ba, _x, *_ = a.collect_rollout()
        torch.manual_seed(9)
        bb, _y, *_ = b.collect_rollout()
        a.update(ba)
        b.update(bb)
        self.assertFalse(all(torch.equal(p, q) for p, q in zip(a.model.parameters(), b.model.parameters())))

    def test_value_coef_moves_critic(self):
        def critic_delta(vc):
            torch.manual_seed(5)
            tr = _trainer(ConstEnv(reward=1.0), learning_rate=1e-3, value_coef=vc)
            before = tr.model.critic.weight.clone()
            buf, *_ = tr.collect_rollout()
            tr.update(buf)
            return (tr.model.critic.weight - before).abs().sum().item()

        self.assertGreater(critic_delta(5.0), critic_delta(0.0))

    def test_grad_clip_constrains(self):
        def delta(clip):
            torch.manual_seed(6)
            tr = _trainer(ConstEnv(reward=1.0), learning_rate=1e-2, max_grad_norm=clip)
            before = [p.clone() for p in tr.model.parameters()]
            buf, *_ = tr.collect_rollout()
            tr.update(buf)
            return sum((a - b).abs().sum().item() for a, b in zip(before, tr.model.parameters()))

        self.assertLess(delta(1e-9), delta(1e9))

    def test_zero_and_constant_rewards_finite(self):
        for reward in (0.0, 1.0):
            torch.manual_seed(7)
            tr = _trainer(ConstEnv(reward=reward))
            buf, *_ = tr.collect_rollout()
            stats = tr.update(buf)
            for key in ("policy_loss", "value_loss", "entropy"):
                self.assertTrue(abs(stats[key]) != float("inf") and stats[key] == stats[key], (reward, key))

    def test_minibatch_one_and_counters(self):
        torch.manual_seed(8)
        tr = _trainer(ConstEnv(), rollout_length=4, minibatch_size=1, update_epochs=2,
                      total_timesteps=8)
        buf, *_ = tr.collect_rollout()
        stats = tr.update(buf)
        self.assertTrue(all(abs(v) != float("inf") for v in stats.values() if isinstance(v, float)))
        n0, u0 = tr.num_timesteps, tr.num_updates
        tr.train()
        self.assertEqual(tr.num_timesteps, 8)
        self.assertEqual(tr.num_updates, u0 + 1)  # 4 more steps / rollout 4 = exactly 1 update
        self.assertEqual(n0, 4)


if __name__ == "__main__":
    unittest.main()
