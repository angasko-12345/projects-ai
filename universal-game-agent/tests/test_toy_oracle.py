"""Tests for the privileged toy-Pong oracle bound and toy rally metrics."""
try:
    from . import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap  # noqa: F401 - imported for its sys.path bootstrap side effect

import json
import unittest
from pathlib import Path

from training.toy_oracle import run_oracle, summarize_toy_eval

_ROOT = Path(__file__).resolve().parent.parent


class TestToyOracle(unittest.TestCase):
    def test_oracle_survives_to_truncation_on_eval_seeds(self):
        report = run_oracle(episodes=20)
        self.assertEqual(report["survived_to_truncation"], 20)
        self.assertEqual(report["miss_rate"], 0.0)
        self.assertEqual(report["episode_lengths"], [500] * 20)
        # Measured 2026-10-25: 12 hits in every 500-step episode.
        self.assertGreaterEqual(min(report["episode_hits"]), 10)

    def test_oracle_is_deterministic(self):
        self.assertEqual(run_oracle(episodes=5), run_oracle(episodes=5))


class TestSummarizeToyEval(unittest.TestCase):
    def test_hits_from_terminal_and_truncated_episodes(self):
        # 16 steps/-1 = first-ball miss; 58 steps/0 = one hit then miss;
        # 500 steps/+12 = truncated rally with 12 hits.
        s = summarize_toy_eval([-1.0, 0.0, 12.0], [16, 58, 500], max_steps=500)
        self.assertEqual(s["episode_hits"], [0, 1, 12])
        self.assertAlmostEqual(s["first_ball_hit_rate"], 2 / 3)
        self.assertAlmostEqual(s["second_ball_hit_rate"], 1 / 3)
        self.assertAlmostEqual(s["miss_rate"], 2 / 3)
        self.assertEqual(s["survived_to_truncation"], 1)

    def test_rejects_inconsistent_input(self):
        with self.assertRaises(ValueError):
            summarize_toy_eval([-2.0], [16])
        with self.assertRaises(ValueError):
            summarize_toy_eval([0.0], [16, 58])

    def test_readme_toy_claim_matches_saved_run(self):
        # README "Toy results": seed 0, first-ball hit rate 60% -> 90%, every
        # eval episode ends in a miss (no truncation, no second hit).
        data = json.loads((_ROOT / "experiments/exp_toy_ppo_01_results.json").read_text(encoding="utf-8"))
        before = summarize_toy_eval(data["baseline_eval"]["episode_rewards"], data["baseline_eval"]["episode_lengths"])
        after = summarize_toy_eval(data["final_eval"]["episode_rewards"], data["final_eval"]["episode_lengths"])
        self.assertAlmostEqual(before["first_ball_hit_rate"], 0.6)
        self.assertAlmostEqual(after["first_ball_hit_rate"], 0.9)
        self.assertEqual(before["miss_rate"], 1.0)
        self.assertEqual(after["miss_rate"], 1.0)
        self.assertEqual(after["second_ball_hit_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
