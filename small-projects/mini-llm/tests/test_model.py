"""Model tests: shapes, loss, causality, generation, checkpoints. CPU only."""

import math
import os
import sys
import tempfile
import unittest

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.generate import generate_tokens
from src.model import MiniGPT, from_config
from src.train import load_checkpoint, save_checkpoint


def tiny_config(**overrides) -> Config:
    kw = dict(
        vocab_size=64,
        context_length=16,
        n_layers=2,
        n_heads=2,
        d_model=32,
        d_ff=64,
        dropout=0.0,
        batch_size=2,
    )
    kw.update(overrides)
    return Config(**kw)


class TestModel(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(0)
        self.cfg = tiny_config()
        self.model = from_config(self.cfg)

    def test_output_shape(self):
        x = torch.randint(0, self.cfg.vocab_size, (2, 8))
        logits, loss = self.model(x)
        self.assertEqual(logits.shape, (2, 8, self.cfg.vocab_size))

    def test_scalar_finite_loss(self):
        x = torch.randint(0, self.cfg.vocab_size, (2, 8))
        y = torch.randint(0, self.cfg.vocab_size, (2, 8))
        _, loss = self.model(x, y)
        self.assertEqual(loss.dim(), 0)
        self.assertTrue(math.isfinite(loss.item()))

    def test_causal_masking(self):
        """Changing a future token must not change logits at earlier positions."""
        torch.manual_seed(1)
        m = from_config(self.cfg)
        m.eval()
        base = torch.randint(0, self.cfg.vocab_size, (1, 8))
        altered = base.clone()
        altered[0, 5] = (altered[0, 5] + 1) % self.cfg.vocab_size
        with torch.no_grad():
            logits_base, _ = m(base)
            logits_alt, _ = m(altered)
        # Positions 0..4 cannot see position 5 -> identical.
        self.assertTrue(torch.equal(logits_base[:, :5, :], logits_alt[:, :5, :]))
        # Position 5+ sees the change -> differs.
        self.assertFalse(torch.equal(logits_base[:, 5:, :], logits_alt[:, 5:, :]))

    def test_weights_tied(self):
        self.assertIs(self.model.lm_head.weight, self.model.wte.weight)

    def test_generation_valid_ids(self):
        self.model.eval()
        prompt = torch.randint(0, self.cfg.vocab_size, (1, 4))
        out = generate_tokens(self.model, prompt, n_tokens=6, temperature=1.0, top_k=5)
        self.assertEqual(out.shape, (1, 10))
        self.assertTrue(((out >= 0) & (out < self.cfg.vocab_size)).all().item())

    def test_checkpoint_loading(self):
        opt = torch.optim.AdamW(self.model.parameters(), lr=1e-3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ckpt.pt")
            save_checkpoint(path, self.model, opt, step=7, cfg=self.cfg, lr=1e-3)
            fresh = from_config(self.cfg)
            ckpt = load_checkpoint(path, fresh)
            self.assertEqual(ckpt["step"], 7)
            self.assertIn("model_state", ckpt)
            self.assertIn("optimizer_state", ckpt)
            self.assertIn("scheduler_state", ckpt)
            self.assertIn("config", ckpt)
            for p_old, p_new in zip(
                self.model.parameters(), fresh.parameters()
            ):
                self.assertTrue(torch.equal(p_old, p_new))


if __name__ == "__main__":
    unittest.main()
