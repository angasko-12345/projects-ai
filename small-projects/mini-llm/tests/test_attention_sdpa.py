"""Attention correctness: SDPA output vs the pre-change manual attention.

The reference functions below are the exact computation CausalSelfAttention
used before scaled_dot_product_attention landed (q @ k^T / sqrt(d), explicit
tril mask, softmax, optional dropout on the weights). They share the module's
weights, so an eval-mode match proves the swap changed nothing but the kernel.
CPU only, dropout disabled unless a test is specifically about dropout.
"""

import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.config import Config
from src.model import CausalSelfAttention, MultiHeadAttention, from_config
from src.train import load_checkpoint, save_checkpoint

ATOL = 1e-5
RTOL = 1e-4


def reference_head(head: CausalSelfAttention, x: torch.Tensor) -> torch.Tensor:
    """The old single-head path: manual scores, mask, softmax, weight dropout."""
    q = head.wq(x)
    k = head.wk(x)
    v = head.wv(x)
    scores = q @ k.transpose(-2, -1) / math.sqrt(q.size(-1))
    t = x.size(1)
    mask = torch.tril(torch.ones(t, t, device=x.device, dtype=torch.bool))
    scores = scores.masked_fill(~mask, float("-inf"))
    weights = torch.softmax(scores, dim=-1)
    return head.drop(weights) @ v


def reference_attention(attn: MultiHeadAttention, x: torch.Tensor) -> torch.Tensor:
    """The old multi-head path: per-head manual attention, cat, proj, dropout."""
    out = torch.cat([reference_head(h, x) for h in attn.heads], dim=-1)
    return attn.drop(attn.proj(out))


def make_attention(d_model: int = 16, n_heads: int = 2, dropout: float = 0.0,
                   seed: int = 0) -> MultiHeadAttention:
    torch.manual_seed(seed)
    attn = MultiHeadAttention(d_model, n_heads, dropout)
    attn.eval()
    return attn


class TestMatchesManualAttention(unittest.TestCase):
    def test_single_head_matches_reference(self):
        torch.manual_seed(0)
        head = CausalSelfAttention(d_model=16, d_head=8).eval()
        x = torch.randn(3, 12, 16)
        with torch.no_grad():
            got = head(x)
            want = reference_head(head, x)
        self.assertTrue(torch.allclose(got, want, atol=ATOL, rtol=RTOL),
                        f"max abs diff {(got - want).abs().max().item():.3e}")

    def test_multi_head_matches_reference(self):
        attn = make_attention(d_model=32, n_heads=4)
        x = torch.randn(2, 16, 32)
        with torch.no_grad():
            got = attn(x)
            want = reference_attention(attn, x)
        self.assertEqual(got.shape, (2, 16, 32))
        self.assertTrue(torch.allclose(got, want, atol=ATOL, rtol=RTOL),
                        f"max abs diff {(got - want).abs().max().item():.3e}")

    def test_matches_reference_for_several_shapes_and_dtypes_contexts(self):
        for d_model, n_heads, t, b in ((16, 2, 1, 1), (48, 6, 8, 2), (24, 3, 20, 3)):
            with self.subTest(d_model=d_model, n_heads=n_heads, t=t, b=b):
                attn = make_attention(d_model, n_heads)
                x = torch.randn(b, t, d_model)
                with torch.no_grad():
                    got = attn(x)
                    want = reference_attention(attn, x)
                self.assertTrue(torch.allclose(got, want, atol=ATOL, rtol=RTOL))

    def test_sdpa_is_the_code_path(self):
        attn = make_attention()
        x = torch.randn(2, 8, 16)
        with mock.patch("src.model.F.scaled_dot_product_attention",
                        wraps=F.scaled_dot_product_attention) as sdpa:
            with torch.no_grad():
                attn(x)
        self.assertTrue(sdpa.called, "attention no longer goes through SDPA")
        for call in sdpa.call_args_list:
            self.assertTrue(call.kwargs.get("is_causal"),
                            "SDPA must be called with a causal mask")


class TestCausalMasking(unittest.TestCase):
    def test_future_tokens_do_not_reach_earlier_positions(self):
        attn = make_attention(d_model=16, n_heads=2)
        base = torch.randn(1, 10, 16)
        altered = base.clone()
        altered[0, 6:] += 5.0
        with torch.no_grad():
            out_base = attn(base)
            out_alt = attn(altered)
        # Positions 0..5 attend only up to themselves, so they are untouched.
        self.assertTrue(torch.equal(out_base[:, :6, :], out_alt[:, :6, :]))
        self.assertFalse(torch.equal(out_base[:, 6:, :], out_alt[:, 6:, :]))

    def test_position_zero_attends_only_to_itself(self):
        # Position 0 has one allowed key, so its softmax weight is 1 and the
        # head output there is exactly v_0 = x_0 Wv.
        head = CausalSelfAttention(16, 8).eval()
        x = torch.randn(1, 8, 16)
        with torch.no_grad():
            out = head(x)
            v = head.wv(x)
        self.assertTrue(torch.allclose(out[:, 0], v[:, 0], atol=ATOL, rtol=RTOL))


class TestDropoutAndModes(unittest.TestCase):
    def test_zero_dropout_train_equals_eval(self):
        head = CausalSelfAttention(16, 8, dropout=0.0)
        x = torch.randn(2, 8, 16)
        head.train()
        with torch.no_grad():
            train_out = head(x)
        head.eval()
        with torch.no_grad():
            eval_out = head(x)
        self.assertTrue(torch.equal(train_out, eval_out))

    def test_eval_is_deterministic_with_dropout_enabled(self):
        attn = make_attention(dropout=0.5)
        x = torch.randn(2, 8, 16)
        with torch.no_grad():
            self.assertTrue(torch.equal(attn(x), attn(x)))

    def test_training_applies_dropout_but_seed_makes_it_repeatable(self):
        head = CausalSelfAttention(16, 8, dropout=0.5)
        x = torch.randn(2, 8, 16)
        head.eval()
        with torch.no_grad():
            eval_out = head(x)
        head.train()
        torch.manual_seed(0)
        a = head(x)
        torch.manual_seed(0)
        b = head(x)
        self.assertTrue(torch.equal(a, b), "same seed must give the same dropout")
        self.assertFalse(torch.allclose(a, eval_out),
                         "training dropout should perturb the output")

    def test_eval_mode_passes_zero_dropout_to_sdpa(self):
        attn = make_attention(dropout=0.5)
        x = torch.randn(2, 8, 16)
        with mock.patch("src.model.F.scaled_dot_product_attention",
                        wraps=F.scaled_dot_product_attention) as sdpa:
            with torch.no_grad():
                attn(x)
        self.assertEqual(sdpa.call_args.kwargs["dropout_p"], 0.0)


class TestGradients(unittest.TestCase):
    def test_gradients_reach_every_attention_parameter(self):
        attn = make_attention(d_model=16, n_heads=2)
        attn.train()
        x = torch.randn(2, 8, 16, requires_grad=True)
        loss = attn(x).pow(2).sum()
        loss.backward()
        self.assertIsNotNone(x.grad)
        self.assertTrue(torch.isfinite(x.grad).all())
        params = {"proj.weight": attn.proj.weight, "proj.bias": attn.proj.bias}
        for i, head in enumerate(attn.heads):
            params[f"heads.{i}.wq.weight"] = head.wq.weight
            params[f"heads.{i}.wk.weight"] = head.wk.weight
            params[f"heads.{i}.wv.weight"] = head.wv.weight
        for name, p in params.items():
            with self.subTest(param=name):
                self.assertIsNotNone(p.grad, f"{name} got no gradient")
                self.assertTrue(torch.isfinite(p.grad).all())
                self.assertGreater(float(p.grad.abs().sum()), 0.0)


class TestCheckpointCompatibility(unittest.TestCase):
    def expected_attention_keys(self) -> set[str]:
        keys = {"blocks.0.attn.proj.weight", "blocks.0.attn.proj.bias"}
        for i in range(2):
            for proj in ("wq", "wk", "wv"):
                keys.add(f"blocks.0.attn.heads.{i}.{proj}.weight")
        return keys

    def cfg(self) -> Config:
        return Config(vocab_size=32, context_length=8, n_layers=1, n_heads=2,
                      d_model=16, d_ff=32)

    def test_parameter_names_are_unchanged(self):
        model = from_config(self.cfg())
        keys = set(model.state_dict().keys())
        self.assertTrue(self.expected_attention_keys() <= keys)
        # No fused-QKV rename slipped in: the per-head projections are still here.
        self.assertFalse(any("qkv" in k for k in keys))
        self.assertFalse(any("in_proj" in k or "q_proj" in k for k in keys))

    def test_state_dict_with_old_layout_loads_strictly(self):
        # A state dict carrying exactly the pre-change keys must load with
        # strict=True, which is the migration-free compatibility guarantee.
        model = from_config(self.cfg())
        self.assertTrue(self.expected_attention_keys() <= set(model.state_dict()))
        fresh = from_config(self.cfg())
        fresh.load_state_dict(model.state_dict(), strict=True)

    def test_checkpoint_round_trip_preserves_attention_weights(self):
        cfg = self.cfg()
        model = from_config(cfg)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ckpt.pt")
            opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
            save_checkpoint(path, model, opt, step=3, cfg=cfg, lr=1e-3)
            fresh = from_config(cfg)
            load_checkpoint(path, fresh)
            for name in self.expected_attention_keys():
                with self.subTest(param=name):
                    self.assertTrue(torch.equal(model.state_dict()[name],
                                                fresh.state_dict()[name]))


if __name__ == "__main__":
    unittest.main()
