"""Tests for training.benchmark throughput measurement."""

try:
    from . import _bootstrap
except ImportError:
    import _bootstrap  # type: ignore[no-redef]

import unittest

try:
    from training.benchmark import BenchEnv, run_benchmark_variant
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestBenchmark(unittest.TestCase):
    def test_sample_count_consistent(self):
        """All variants process the same total timesteps with correct update counts."""
        total = 8
        rollout = 2
        for n in (1, 2, 4):
            r = run_benchmark_variant(n, total, rollout, seed=0, warmup=False)
            self.assertEqual(
                r["total_timesteps"], total,
                f"{n}-env: got {r['total_timesteps']}, expected {total}",
            )
            expected_updates = total // (rollout * n)
            self.assertEqual(
                r["updates"], expected_updates,
                f"{n}-env: got {r['updates']} updates, expected {expected_updates}",
            )

    def test_reset_counts(self):
        """Each env reset exactly once (initial reset, no terminations)."""
        for n in (1, 2, 4):
            r = run_benchmark_variant(n, 8, 2, seed=0, warmup=False)
            self.assertEqual(r["resets"], n, f"{n}-env should have {n} resets")

    def test_metrics_finite_and_positive(self):
        """All timing-derived metrics are positive and finite."""
        r = run_benchmark_variant(1, 8, 2, seed=0, warmup=False)
        self.assertGreater(r["elapsed"], 0)
        self.assertGreater(r["env_steps_per_sec"], 0)
        self.assertGreater(r["updates"], 0)
        self.assertGreaterEqual(r["updates_per_sec"], 0)
        self.assertGreaterEqual(r["resets"], 1)

    def test_benchenv_api(self):
        """BenchEnv satisfies the Gymnasium-style interface PPOTrainer needs."""
        env = BenchEnv([(0.0, False, False)] * 10, seed=0)
        obs, info = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype.name, "float32")

        obs2, reward, term, trunc, info2 = env.step(0)
        self.assertEqual(obs2.shape, (4, 84, 84))
        self.assertEqual(reward, 0.0)
        self.assertFalse(term)
        self.assertFalse(trunc)
        env.close()

    def test_benchenv_terminates_when_scripted(self):
        """BenchEnv returns scripted termination/truncation flags."""
        env = BenchEnv([(0.5, True, False)], seed=42)
        env.reset(seed=0)
        _, reward, term, trunc, _ = env.step(0)
        self.assertEqual(reward, 0.5)
        self.assertTrue(term)
        self.assertFalse(trunc)
        env.close()


if __name__ == "__main__":
    unittest.main()
