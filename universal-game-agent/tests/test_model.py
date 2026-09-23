"""Tests for the recurrent actor-critic (requires torch)."""
import tempfile
import unittest
from pathlib import Path

try:
    import torch

    from agent.model import ActorCritic

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


@unittest.skipUnless(_HAS_TORCH, "torch not installed")
class TestActorCritic(unittest.TestCase):
    def test_forward_shapes(self):
        model = ActorCritic(num_actions=3).eval()
        hidden = model.initial_state(2)
        with torch.no_grad():
            logits, value, new_hidden = model(torch.rand(2, 4, 84, 84), hidden)
        self.assertEqual(tuple(logits.shape), (2, 3))
        self.assertEqual(tuple(value.shape), (2,))
        self.assertEqual(tuple(new_hidden.shape), (1, 2, 128))

    def test_configurable_dims(self):
        model = ActorCritic(
            num_actions=5, in_channels=2, frame_size=42,
            feature_dim=64, hidden_size=32, num_layers=2,
        ).eval()
        hidden = model.initial_state(3)
        with torch.no_grad():
            logits, value, new_hidden = model(torch.rand(3, 2, 42, 42), hidden)
        self.assertEqual(tuple(logits.shape), (3, 5))
        self.assertEqual(tuple(value.shape), (3,))
        self.assertEqual(tuple(new_hidden.shape), (2, 3, 32))

    def test_batched_matches_single(self):
        torch.manual_seed(1)
        model = ActorCritic().eval()
        obs = torch.rand(4, 4, 84, 84)
        with torch.no_grad():
            batch_logits, batch_value, _ = model(obs, model.initial_state(4))
            for i in range(4):
                l, v, _ = model(obs[i : i + 1], model.initial_state(1))
                self.assertTrue(torch.allclose(batch_logits[i], l[0], atol=1e-5))
                self.assertTrue(torch.allclose(batch_value[i], v[0], atol=1e-5))

    def test_hidden_state_carries_and_resets(self):
        torch.manual_seed(2)
        model = ActorCritic().eval()
        obs = torch.rand(2, 4, 84, 84)
        h0 = model.initial_state(2)
        self.assertTrue(torch.all(h0 == 0))
        with torch.no_grad():
            _, _, h1 = model(obs, h0)
            _, _, h2 = model(obs, h1)
        self.assertFalse(torch.allclose(h1, h0))
        self.assertFalse(torch.allclose(h2, h1))
        with torch.no_grad():
            fresh_logits, _, _ = model(obs, model.initial_state(2))
            carried_logits, _, _ = model(obs, h1)
        self.assertFalse(torch.allclose(fresh_logits, carried_logits))

    def test_save_load_roundtrip(self):
        torch.manual_seed(3)
        model = ActorCritic(num_actions=3, hidden_size=64).eval()
        obs = torch.rand(2, 4, 84, 84)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "model.pt")
            model.save(path)
            restored = ActorCritic.load(path)
        for a, b in zip(model.parameters(), restored.parameters()):
            self.assertTrue(torch.equal(a, b))
        self.assertEqual(restored._config["hidden_size"], 64)
        with torch.no_grad():
            expected = model(obs, model.initial_state(2))
            actual = restored.eval()(obs, restored.initial_state(2))
        for e, a in zip(expected, actual):
            self.assertTrue(torch.allclose(e, a))

    def test_invalid_inputs(self):
        model = ActorCritic()
        good_hidden = model.initial_state(2)
        with self.assertRaises(ValueError):
            model(torch.rand(2, 4, 84), good_hidden)  # 3D
        with self.assertRaises(ValueError):
            model(torch.rand(2, 3, 84, 84), good_hidden)  # wrong channels
        with self.assertRaises(ValueError):
            model(torch.rand(2, 4, 84, 84), torch.zeros(1, 3, 128))  # bad hidden
        with self.assertRaises(ValueError):
            ActorCritic(num_actions=0)
        with self.assertRaises(ValueError):
            model.initial_state(0)


if __name__ == "__main__":
    unittest.main()
