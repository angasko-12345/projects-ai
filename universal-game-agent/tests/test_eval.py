"""Tests for evaluation + experiment wiring (requires torch)."""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import tempfile
import unittest
from pathlib import Path

try:
    import torch

    from agent.model import ActorCritic
    from environment.preprocessing import PreprocessingWrapper
    from environment.toy_pong import ToyPongEnv
    from training.evaluate import evaluate
    from training.experiment import load_experiment, make_env_from_config

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _make_env():
    return PreprocessingWrapper(ToyPongEnv(max_steps=64))


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestEvaluate(unittest.TestCase):
    def test_report_keys_and_episodes_finish(self):
        torch.manual_seed(0)
        model = ActorCritic().eval()
        rep = evaluate(model, _make_env, episodes=4, seeds=[0, 1, 2, 3])
        for key in ("mean_reward", "std_reward", "mean_length", "episode_rewards", "episode_lengths"):
            self.assertIn(key, rep)
        self.assertEqual(len(rep["episode_rewards"]), 4)
        self.assertTrue(all(s >= 1 for s in rep["episode_lengths"]))

    def test_deterministic_same_seeds(self):
        torch.manual_seed(0)
        model = ActorCritic().eval()
        first = evaluate(model, _make_env, episodes=4, seeds=[5, 6, 7, 8])
        second = evaluate(model, _make_env, episodes=4, seeds=[5, 6, 7, 8])
        self.assertEqual(first["episode_rewards"], second["episode_rewards"])

    def test_invalid_args(self):
        model = ActorCritic().eval()
        with self.assertRaises(ValueError):
            evaluate(model, _make_env, episodes=0)
        with self.assertRaises(ValueError):
            evaluate(model, _make_env, episodes=2, seeds=[1])

    def test_experiment_config_loads(self):
        cfg = load_experiment(Path(__file__).resolve().parent.parent / "experiments" / "exp_toy_ppo_01.yaml")
        for section in ("env", "model", "ppo", "eval"):
            self.assertIn(section, cfg)
        env = make_env_from_config(cfg["env"])()
        obs, _ = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))

    def test_experiment_config_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("ppo: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_experiment(bad)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestEvalMode(unittest.TestCase):
    def test_mode_preserved(self):
        from training.evaluate import evaluate as evaluate_fn

        torch.manual_seed(0)
        model = ActorCritic()
        model.train()
        evaluate_fn(model, _make_env, episodes=2, seeds=[0, 1])
        self.assertTrue(model.training)
        model.eval()
        evaluate_fn(model, _make_env, episodes=2, seeds=[0, 1])
        self.assertFalse(model.training)


class ScriptedEvalEnv:
    """Seed-selected scripted episodes with marked frames (test-only fake).

    Declares no reward semantics, so it is treated as ``generic``: its
    scripted rewards (1.0, 2.0, 5.0) are not hit/miss events.
    """

    RESET_VAL = -1.0
    SCRIPTS = {0: [(1.0, False, False), (2.0, True, False)],
               1: [(5.0, False, True)]}

    def __init__(self, obs_shape=(1, 84, 84)):
        self.shape = obs_shape
        self.script = []
        self.t = 0
        self.recorded_actions = []
        self.action_space = type("Space", (), {"n": 2})()

    def reset(self, seed=None, options=None):
        import numpy as np

        self.script = self.SCRIPTS[(int(seed) % 2) if seed is not None else 0]
        self.t = 0
        return np.full(self.shape, self.RESET_VAL, dtype=np.float32), {}

    def step(self, action):
        import numpy as np

        assert 0 <= int(action) < 2, f"invalid action {action!r}"
        r, term, trunc = self.script[self.t] if self.t < len(self.script) else (0.0, True, False)
        self.t += 1
        self.recorded_actions.append(int(action))
        return np.full(self.shape, float(self.t), dtype=np.float32), r, term, trunc, {}

    def close(self):
        pass


class SignScriptedEvalEnv(ScriptedEvalEnv):
    """Scripted env that declares the sign-based hit/miss contract (ROOT-036)."""

    reward_semantics = "sign"


def _tiny_model():
    torch.manual_seed(0)
    return ActorCritic(num_actions=2, in_channels=1, feature_dim=16, hidden_size=8)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestEvalCorrectness(unittest.TestCase):
    #: Keys every report carries, whatever the reward semantics.
    BASE_KEYS = {"episodes", "greedy", "seeds", "reward_semantics", "mean_reward",
                 "std_reward", "min_reward", "max_reward", "mean_length",
                 "episode_rewards", "episode_lengths", "action_counts",
                 "episode_positive_reward_steps", "episode_negative_reward_steps",
                 "mean_positive_reward_steps", "mean_negative_reward_steps",
                 "episode_terminated", "episode_truncated",
                 "terminated_episodes", "truncated_episodes"}
    #: Hit/miss keys exist only when the env declares sign-based rewards.
    SIGN_KEYS = {"episode_hits", "episode_misses", "mean_hits", "mean_misses"}

    def test_attribution_and_schema(self):
        from training.evaluate import evaluate as evaluate_fn

        rep = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(set(rep.keys()), self.BASE_KEYS)
        self.assertEqual(rep["episode_rewards"], [3.0, 5.0])
        self.assertEqual(rep["episode_lengths"], [2, 1])
        self.assertAlmostEqual(rep["mean_reward"], 4.0)
        self.assertAlmostEqual(rep["mean_length"], 1.5)
        self.assertEqual(rep["min_reward"], 3.0)
        self.assertEqual(rep["max_reward"], 5.0)

    def test_sign_semantics_schema_adds_hit_keys(self):
        from training.evaluate import evaluate as evaluate_fn

        rep = evaluate_fn(_tiny_model(), SignScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(set(rep.keys()), self.BASE_KEYS | self.SIGN_KEYS)
        self.assertEqual(rep["reward_semantics"], "sign")

    def test_greedy_ignores_rng_state(self):
        from training.evaluate import evaluate as evaluate_fn

        torch.manual_seed(11)
        first = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        torch.manual_seed(999)
        second = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(first["episode_rewards"], second["episode_rewards"])

    def test_sampled_valid_and_rng_driven(self):
        from training.evaluate import evaluate as evaluate_fn

        made = []

        def make():
            env = ScriptedEvalEnv()
            made.append(env)
            return env

        torch.manual_seed(3)
        rep = evaluate_fn(_tiny_model(), make, episodes=2, seeds=[0, 1], greedy=False)
        self.assertEqual(len(made), 2)
        for env in made:
            self.assertTrue(env.recorded_actions)
            self.assertTrue(all(a in (0, 1) for a in env.recorded_actions))
        torch.manual_seed(3)
        again = evaluate_fn(_tiny_model(), make, episodes=2, seeds=[0, 1], greedy=False)
        self.assertEqual(rep["episode_rewards"], again["episode_rewards"])

    def test_separate_envs_training_untouched(self):
        from training.evaluate import evaluate as evaluate_fn

        class CountingEnv(ScriptedEvalEnv):
            steps = 0

            def step(self, action):
                CountingEnv.steps += 1
                return super().step(action)

        CountingEnv.steps = 0
        train_env = CountingEnv()
        before = CountingEnv.steps
        evaluate_fn(_tiny_model(), CountingEnv, episodes=3, seeds=[0, 1, 0])
        self.assertEqual(CountingEnv.steps - before, 2 + 1 + 2)  # only eval envs stepped
        self.assertEqual(train_env.t, 0)  # the training env was never touched

    def test_grads_disabled_and_params_frozen(self):
        from training.evaluate import evaluate as evaluate_fn

        model = _tiny_model()
        before = [p.clone() for p in model.parameters()]
        evaluate_fn(model, ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        for a, b in zip(before, model.parameters()):
            self.assertTrue(torch.equal(a, b))
            self.assertIsNone(b.grad)

    def test_recurrent_reset_each_episode(self):
        from training.evaluate import evaluate as evaluate_fn

        model = _tiny_model()
        calls = []
        orig = model.forward

        def spy(obs, hidden):
            calls.append(hidden.clone())
            return orig(obs, hidden)

        model.forward = spy
        evaluate_fn(model, ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        # seed0 runs 2 steps, seed1 runs 1 step; first call of each episode is zero
        self.assertEqual(len(calls), 3)
        self.assertTrue(bool((calls[0] == 0).all()))
        self.assertTrue(bool((calls[2] == 0).all()))

    def test_single_episode(self):
        from training.evaluate import evaluate as evaluate_fn

        rep = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=1, seeds=[1])
        self.assertEqual(rep["episode_rewards"], [5.0])
        self.assertEqual(rep["episode_lengths"], [1])
        self.assertEqual(rep["std_reward"], 0.0)

    def test_hits_and_misses_counted_for_sign_provider(self):
        from training.evaluate import evaluate as evaluate_fn

        rep = evaluate_fn(_tiny_model(), SignScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(rep["episode_hits"], [2, 1])
        self.assertEqual(rep["episode_misses"], [0, 0])
        self.assertEqual(rep["mean_hits"], 1.5)
        self.assertEqual(rep["mean_misses"], 0.0)

    def test_generic_provider_reports_no_hits(self):
        """ROOT-036: a reward with no hit/miss meaning must not be called hits."""
        from training.evaluate import evaluate as evaluate_fn

        rep = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(rep["reward_semantics"], "generic")
        for key in ("mean_hits", "mean_misses", "episode_hits", "episode_misses"):
            self.assertNotIn(key, rep,
                             f"{key!r} must be absent, not zero, for generic rewards")
        # the same counts stay available under a name that claims no event
        self.assertEqual(rep["episode_positive_reward_steps"], [2, 1])
        self.assertEqual(rep["episode_negative_reward_steps"], [0, 0])
        self.assertAlmostEqual(rep["mean_positive_reward_steps"], 1.5)
        self.assertAlmostEqual(rep["mean_negative_reward_steps"], 0.0)

    def test_survival_style_rewards_are_generic_not_hits(self):
        """Every step pays +1: the step count is not a hit count."""
        from training.evaluate import evaluate as evaluate_fn

        class SurvivalScriptEnv(ScriptedEvalEnv):
            SCRIPTS = {0: [(1.0, False, False), (1.0, False, False), (1.0, True, False)],
                       1: [(1.0, False, False)]}

        rep = evaluate_fn(_tiny_model(), SurvivalScriptEnv, episodes=2, seeds=[0, 1])
        self.assertNotIn("mean_hits", rep)
        self.assertEqual(rep["episode_positive_reward_steps"], [3, 1])

    def test_unknown_semantics_warns_and_degrades_to_generic(self):
        from training.evaluate import evaluate as evaluate_fn

        class TypoEnv(ScriptedEvalEnv):
            reward_semantics = "signn"

        with self.assertWarns(UserWarning):
            rep = evaluate_fn(_tiny_model(), TypoEnv, episodes=1, seeds=[0])
        self.assertEqual(rep["reward_semantics"], "generic")
        self.assertNotIn("mean_hits", rep)

    def test_disagreeing_envs_fall_back_to_generic(self):
        """A mixed run cannot make the stronger hit/miss claim."""
        from training.evaluate import evaluate as evaluate_fn

        envs = [SignScriptedEvalEnv(), ScriptedEvalEnv()]

        rep = evaluate_fn(_tiny_model(), lambda: envs.pop(0), episodes=2, seeds=[0, 1])
        self.assertEqual(rep["reward_semantics"], "generic")
        self.assertNotIn("mean_hits", rep)

    def test_terminated_truncated_counts(self):
        from training.evaluate import evaluate as evaluate_fn

        # seed 0 ends terminated, seed 1 ends truncated (see SCRIPTS above).
        rep = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 1])
        self.assertEqual(rep["episode_terminated"], [True, False])
        self.assertEqual(rep["episode_truncated"], [False, True])
        self.assertEqual(rep["terminated_episodes"], 1)
        self.assertEqual(rep["truncated_episodes"], 1)
        rep2 = evaluate_fn(_tiny_model(), ScriptedEvalEnv, episodes=2, seeds=[0, 0])
        self.assertEqual(rep2["terminated_episodes"], 2)
        self.assertEqual(rep2["truncated_episodes"], 0)

    def test_comparison_summaries_and_difference(self):
        from training.evaluate import evaluate as evaluate_fn
        from training.external_experiment import summarize_difference, summarize_eval

        base = summarize_eval(evaluate_fn(_tiny_model(), SignScriptedEvalEnv,
                                          episodes=2, seeds=[0, 1]))
        for key in ("mean_reward", "std_reward", "mean_hits", "mean_misses",
                    "mean_length", "terminated_episodes", "truncated_episodes",
                    "action_counts"):
            self.assertIn(key, base)
        self.assertEqual(base["terminated_episodes"], 1)
        self.assertEqual(base["truncated_episodes"], 1)
        self.assertAlmostEqual(sum(base["action_share"].values()), 1.0)
        diff = summarize_difference(base, base)
        for key in ("mean_reward", "mean_hits", "mean_misses", "mean_length",
                    "terminated_episodes", "truncated_episodes"):
            self.assertEqual(diff[key], 0.0)
        self.assertTrue(all(v == 0 for v in diff["action_counts"].values()))

    def test_comparison_of_generic_rewards_omits_hit_metrics(self):
        """ROOT-036: a generic-reward comparison must not report hit deltas."""
        from training.evaluate import evaluate as evaluate_fn
        from training.external_experiment import summarize_difference, summarize_eval

        untrained = summarize_eval(evaluate_fn(_tiny_model(), ScriptedEvalEnv,
                                               episodes=2, seeds=[0, 1]))
        trained = summarize_eval(evaluate_fn(_tiny_model(), ScriptedEvalEnv,
                                            episodes=2, seeds=[0, 1]))
        self.assertEqual(untrained["reward_semantics"], "generic")
        self.assertNotIn("mean_hits", untrained)
        diff = summarize_difference(untrained, trained)
        self.assertNotIn("mean_hits", diff)
        self.assertNotIn("mean_misses", diff)
        self.assertIn("mean_reward", diff)
        self.assertEqual(diff["reward_semantics"], "generic")

    def test_comparison_of_mixed_semantics_is_flagged(self):
        from training.evaluate import evaluate as evaluate_fn
        from training.external_experiment import summarize_difference, summarize_eval

        sign = summarize_eval(evaluate_fn(_tiny_model(), SignScriptedEvalEnv,
                                          episodes=2, seeds=[0, 1]))
        generic = summarize_eval(evaluate_fn(_tiny_model(), ScriptedEvalEnv,
                                             episodes=2, seeds=[0, 1]))
        diff = summarize_difference(generic, sign)
        self.assertEqual(diff["reward_semantics"], "mixed")
        # only the metrics both sides carry are compared
        self.assertNotIn("mean_hits", diff)


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestExperimentWorkflow(unittest.TestCase):
    def test_full_workflow_files_and_schema(self):
        import tempfile
        import yaml

        from training.experiment import run_experiment

        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "env": {"width": 64, "height": 48, "max_steps": 64, "obs_size": 84,
                        "num_stack": 4, "skip": 1},
                "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 64,
                          "hidden_size": 32, "num_layers": 1},
                "ppo": {"total_timesteps": 64, "rollout_length": 32, "minibatch_size": 32,
                        "update_epochs": 1, "learning_rate": 1e-3, "seed": 0,
                        "checkpoint_dir": str(Path(tmp) / "ckpt"),
                        "checkpoint_every_updates": 100},
                "eval": {"episodes": 2},
            }
            cfg_path = Path(tmp) / "exp.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            report = run_experiment(cfg_path)
            self.assertTrue((Path(tmp) / "exp_results.json").exists())
            self.assertTrue((Path(tmp) / "ckpt" / "ppo_final.pt").exists())
            for key in ("initial_mean_episode_reward", "final_eval_mean_reward",
                        "final_eval_mean_episode_length", "training_steps",
                        "training_seconds", "fps", "baseline_eval", "final_eval"):
                self.assertIn(key, report)
            self.assertEqual(report["training_steps"], 64)
            self.assertEqual(report["baseline_eval"]["episodes"], 2)
            self.assertTrue(report["training_seconds"] >= 0.0)
            self.assertTrue(report["fps"] > 0.0)
            self.assertEqual(report["final_eval"]["seeds"], report["baseline_eval"]["seeds"])
            self.assertFalse(report["curiosity_enabled"])


if __name__ == "__main__":
    unittest.main()
