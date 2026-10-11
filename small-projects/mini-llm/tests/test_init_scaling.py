"""Depth-safe initialization: residual output projections scale as 0.02/sqrt(2*L).

The scaling audit (docs/AUDIT-scaling-2026-10-10.md, P0-5) found that every
nn.Linear drew from normal_(std=0.02) with no residual scaling, so the two
branches each block adds to the stream grew the residual variance with depth.
These tests pin the GPT-2 rule on the two projections that carry those
branches (attn.proj, mlp.fc2) at depths the default suite never builds, plus
finiteness, reproducibility, and state-dict compatibility.
"""

import math
import os
import sys
import tempfile
import unittest

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.model import MiniGPT

DEPTHS = (6, 12, 24, 48)
BASE_STD = 0.02
# 4096-8192 samples per weight tensor put the sampled std within ~2% at 3sigma;
# 12% is loose against hardware/lib float noise yet fails loudly on a wrong
# depth, a missing 1/sqrt(2L), or scaling applied to the wrong layer.
TOL = 0.12


def build(n_layers: int, seed: int = 0) -> MiniGPT:
    torch.manual_seed(seed)
    return MiniGPT(vocab_size=64, context_length=16, n_layers=n_layers, n_heads=4,
                   d_model=64, d_ff=128, dropout=0.0)


def fixed_batch():
    torch.manual_seed(0)
    x = torch.randint(0, 64, (2, 16))
    y = torch.randint(0, 64, (2, 16))
    return x, y


class TestDepthScalingFormula(unittest.TestCase):
    def assertRelClose(self, got: float, expected: float) -> None:
        self.assertLess(abs(got - expected) / expected, TOL,
                        f"std {got:.6f} not within {TOL:.0%} of {expected:.6f}")

    def test_residual_projections_follow_the_depth_formula(self):
        for n_layers in DEPTHS:
            model = build(n_layers)
            expected = BASE_STD / math.sqrt(2 * n_layers)
            for i, block in enumerate(model.blocks):
                with self.subTest(n_layers=n_layers, block=i):
                    self.assertRelClose(block.attn.proj.weight.std().item(), expected)
                    self.assertRelClose(block.mlp.fc2.weight.std().item(), expected)
                    self.assertEqual(block.attn.proj.bias.abs().sum().item(), 0.0)
                    self.assertEqual(block.mlp.fc2.bias.abs().sum().item(), 0.0)

    def test_unscaled_layers_keep_the_base_std(self):
        model = build(48)
        for name, tensor in (("wte", model.wte.weight), ("wpe", model.wpe.weight),
                             ("wq", model.blocks[0].attn.heads[0].wq.weight),
                             ("wk", model.blocks[0].attn.heads[0].wk.weight),
                             ("fc1", model.blocks[0].mlp.fc1.weight)):
            with self.subTest(layer=name):
                self.assertRelClose(tensor.std().item(), BASE_STD)
        # The scaled projections are clearly separated from the base-std layers.
        proj = model.blocks[0].attn.proj.weight.std().item()
        wq = model.blocks[0].attn.heads[0].wq.weight.std().item()
        self.assertLess(proj, 0.5 * wq)

    def test_scaling_is_depth_dependent_not_a_constant(self):
        stds = {n: build(n).blocks[0].attn.proj.weight.std().item()
                for n in (6, 12, 48)}
        self.assertGreater(stds[6], stds[12])
        self.assertGreater(stds[12], stds[48])
        # 0.02/sqrt(2*6) vs 0.02/sqrt(2*48): the ratio must be sqrt(48/6).
        self.assertRelClose(stds[6] / stds[48], math.sqrt(48 / 6))


class TestFiniteForwardAndBackwardAtDepth(unittest.TestCase):
    def test_forward_loss_and_gradients_are_finite_at_every_depth(self):
        for n_layers in DEPTHS:
            with self.subTest(n_layers=n_layers):
                model = build(n_layers)
                x, y = fixed_batch()
                logits, loss = model(x, y)
                self.assertTrue(torch.isfinite(logits).all().item())
                self.assertTrue(math.isfinite(loss.item()))
                loss.backward()
                for name, param in model.named_parameters():
                    self.assertIsNotNone(param.grad, name)
                    self.assertTrue(torch.isfinite(param.grad).all().item(), name)
                # Every part of the residual path must actually receive a signal.
                self.assertGreater(model.wte.weight.grad.norm().item(), 0.0)
                self.assertGreater(model.ln_f.weight.grad.norm().item(), 0.0)
                for i, block in enumerate(model.blocks):
                    for part in (block.ln1.weight, block.attn.heads[0].wq.weight,
                                 block.attn.proj.weight, block.ln2.weight,
                                 block.mlp.fc1.weight, block.mlp.fc2.weight):
                        self.assertGreater(part.grad.norm().item(), 0.0,
                                           f"block {i}")

    def test_short_training_run_stays_finite_at_L24(self):
        """The audit's requested deep-config smoke test: a few steps, finite loss."""
        model = build(24)
        x, y = fixed_batch()
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
        for step in range(5):
            opt.zero_grad()
            _, loss = model(x, y)
            loss.backward()
            self.assertTrue(math.isfinite(loss.item()), f"step {step}")
            opt.step()
            for param in model.parameters():
                self.assertTrue(torch.isfinite(param).all().item(), f"step {step}")


class TestSeededReproducibility(unittest.TestCase):
    def test_same_seed_yields_identical_weights(self):
        a, b = build(12, seed=7), build(12, seed=7)
        self.assertEqual(set(a.state_dict()), set(b.state_dict()))
        for key in a.state_dict():
            self.assertTrue(torch.equal(a.state_dict()[key], b.state_dict()[key]), key)

    def test_different_seed_yields_different_weights(self):
        a, b = build(12, seed=7), build(12, seed=8)
        self.assertFalse(torch.equal(a.blocks[0].attn.proj.weight,
                                     b.blocks[0].attn.proj.weight))


class TestCheckpointCompatibility(unittest.TestCase):
    def expected_keys(self, n_layers: int) -> set[str]:
        # lm_head.weight appears even though it is tied to wte.weight: state_dict
        # records both names for the shared tensor.
        keys = {"wte.weight", "wpe.weight", "ln_f.weight", "ln_f.bias",
                "lm_head.weight"}
        for i in range(n_layers):
            prefix = f"blocks.{i}."
            keys.add(prefix + "ln1.weight")
            keys.add(prefix + "ln1.bias")
            keys.add(prefix + "ln2.weight")
            keys.add(prefix + "ln2.bias")
            keys.add(prefix + "attn.proj.weight")
            keys.add(prefix + "attn.proj.bias")
            keys.add(prefix + "mlp.fc1.weight")
            keys.add(prefix + "mlp.fc1.bias")
            keys.add(prefix + "mlp.fc2.weight")
            keys.add(prefix + "mlp.fc2.bias")
            for h in range(4):
                for w in ("wq", "wk", "wv"):
                    keys.add(prefix + f"attn.heads.{h}.{w}.weight")
        return keys

    def test_state_dict_keys_and_shapes_are_unchanged(self):
        """Init scaling must not add, rename, or reshape a single parameter."""
        model = build(6)
        self.assertEqual(set(model.state_dict()), self.expected_keys(6))
        self.assertEqual(model.blocks[0].attn.proj.weight.shape, (64, 64))
        self.assertEqual(model.blocks[0].mlp.fc2.weight.shape, (64, 128))
        self.assertEqual(model.lm_head.weight.shape, (64, 64))

    def test_strict_load_of_a_preexisting_format_checkpoint(self):
        """A state dict in the pre-scaling format loads strict into a new model.

        The init change touches values only, so an old checkpoint's keys and
        shapes must satisfy strict=True with no missing or unexpected entries.
        """
        old_format = build(6)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "model_state.pt")
            torch.save(old_format.state_dict(), path)
            loaded = torch.load(path, weights_only=True)
        fresh = build(6, seed=99)
        result = fresh.load_state_dict(loaded, strict=True)
        self.assertEqual(list(result.missing_keys), [])
        self.assertEqual(list(result.unexpected_keys), [])
        for key, value in fresh.state_dict().items():
            self.assertTrue(torch.equal(value, loaded[key]), key)

    def test_weight_tying_survives_the_scaling_loop(self):
        model = build(6)
        self.assertIs(model.lm_head.weight, model.wte.weight)
        self.assertEqual(model.lm_head.weight.data_ptr(), model.wte.weight.data_ptr())


if __name__ == "__main__":
    unittest.main()

