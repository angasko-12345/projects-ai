"""Stage 4: gradient accumulation.

batch_size is the microbatch size; grad_accum_steps microbatches are summed into
one optimizer update, so the effective batch is batch_size * grad_accum_steps.
These tests pin what makes it correct: the accumulated gradient equals a single
pass over the combined batch, and every counter (optimizer steps, lr schedule,
eval interval, tokens, checkpoints) still counts optimizer updates.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prepare_data
from src.config import Config, load_data_meta
from src.model import from_config
from src.train import apply_cli_config, make_parser, read_checkpoint, train
from src.train import main as train_main

CORPUS = (
    "The tiny language model reads a sequence of tokens and guesses the next one.\n\n"
    "Causal self attention lets every position look only at earlier positions.\n\n"
    "A transformer block has skip connections that add the input back.\n\n"
) * 12


def build_data(tmp: str, context_length: int = 16) -> str:
    """Run prepare_data.main() over a temporary corpus; return the train .bin path."""
    corpus = os.path.join(tmp, "corpus.txt")
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(CORPUS)
    argv = sys.argv
    sys.argv = [
        "prepare_data.py",
        "--input", corpus,
        "--tokenizer-out", os.path.join(tmp, "tokenizer.json"),
        "--train-out", os.path.join(tmp, "train.bin"),
        "--val-out", os.path.join(tmp, "val.bin"),
        "--vocab-size", "256",
        "--min-frequency", "1",
        "--val-frac", "0.2",
        "--context-length", str(context_length),
    ]
    try:
        prepare_data.main()
    finally:
        sys.argv = argv
    return os.path.join(tmp, "train.bin")


class TestGradAccumConfig(unittest.TestCase):
    def test_default_is_one_microbatch_per_update(self):
        self.assertEqual(Config().grad_accum_steps, 1)

    def test_the_field_is_stored_with_the_config(self):
        self.assertIn("grad_accum_steps", Config().to_dict())

    def test_non_positive_and_non_int_values_are_rejected(self):
        for bad in (0, -1, 1.5, "2"):
            with self.subTest(grad_accum_steps=bad):
                with self.assertRaises(AssertionError) as ctx:
                    Config(grad_accum_steps=bad)
                self.assertIn("grad_accum_steps", str(ctx.exception))


class TestGradAccumCli(unittest.TestCase):
    def test_flag_defaults_to_none(self):
        self.assertIsNone(make_parser().parse_args([]).grad_accum_steps)

    def test_flag_sets_the_field(self):
        cfg = Config()
        apply_cli_config(cfg, make_parser().parse_args(["--grad-accum-steps", "4"]))
        self.assertEqual(cfg.grad_accum_steps, 4)

    def test_bare_invocation_leaves_the_default(self):
        cfg = Config()
        before = cfg.to_dict()
        apply_cli_config(cfg, make_parser().parse_args([]))
        self.assertEqual(cfg.to_dict(), before)


class TestGradientEquivalence(unittest.TestCase):
    """The gradient accumulated over N microbatches equals one pass on the batch."""

    def model(self, cfg: Config):
        torch.manual_seed(0)  # identical init for both models
        return from_config(cfg)

    def test_accumulated_gradient_matches_one_big_batch(self):
        cfg = Config(vocab_size=32, context_length=8, n_layers=1, n_heads=2,
                     d_model=16, d_ff=32, dropout=0.0)
        x_all = torch.randint(0, cfg.vocab_size, (4, cfg.context_length))
        y_all = torch.randint(0, cfg.vocab_size, (4, cfg.context_length))

        big = self.model(cfg)
        _, loss = big(x_all, y_all)
        loss.backward()
        big_grads = [p.grad.clone() for p in big.parameters()]

        accum = 2
        split = self.model(cfg)
        split.zero_grad()
        for i in range(accum):
            x = x_all[i * 2:(i + 1) * 2]
            y = y_all[i * 2:(i + 1) * 2]
            _, micro_loss = split(x, y)
            (micro_loss / accum).backward()

        for name, (g_big, p) in enumerate(zip(big_grads, split.parameters())):
            self.assertTrue(
                torch.allclose(g_big, p.grad, atol=1e-6),
                f"gradient mismatch on parameter {name}",
            )

    def test_accumulation_without_scaling_would_not_match(self):
        """Guards the 1/accum scaling: three unscaled microbatches overshoot."""
        cfg = Config(vocab_size=32, context_length=8, n_layers=1, n_heads=2,
                     d_model=16, d_ff=32, dropout=0.0)
        x_all = torch.randint(0, cfg.vocab_size, (4, cfg.context_length))
        y_all = torch.randint(0, cfg.vocab_size, (4, cfg.context_length))

        big = self.model(cfg)
        _, loss = big(x_all, y_all)
        loss.backward()

        accum = 2
        unscaled = self.model(cfg)
        unscaled.zero_grad()
        for i in range(accum):
            _, micro_loss = unscaled(x_all[i * 2:(i + 1) * 2], y_all[i * 2:(i + 1) * 2])
            micro_loss.backward()

        big_norm = torch.sqrt(sum(p.grad.pow(2).sum() for p in big.parameters()))
        unscaled_norm = torch.sqrt(sum(p.grad.pow(2).sum() for p in unscaled.parameters()))
        self.assertGreater(float(unscaled_norm / big_norm), 1.5)


class TestTrainingLoopAccumulation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=3, warmup_steps=1, eval_interval=1,
                  eval_batches=1, grad_accum_steps=2, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def adam_step(self, path: str) -> int:
        state = read_checkpoint(path)["optimizer_state"]["state"]
        first_key = next(iter(state))
        return int(state[first_key]["step"])

    def test_optimizer_updates_count_steps_not_microbatches(self):
        cfg = self.tiny()
        train(cfg)
        final = os.path.join(self.ckpt_dir, "final.pt")
        self.assertEqual(read_checkpoint(final)["step"], cfg.max_steps)
        # One update per step, not one per microbatch.
        self.assertEqual(self.adam_step(final), cfg.max_steps)

    def test_tokens_seen_counts_every_microbatch(self):
        cfg = self.tiny()
        train(cfg)
        progress = read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["checkpoint_metadata"]["progress"]
        expected = cfg.max_steps * cfg.grad_accum_steps * cfg.batch_size * cfg.context_length
        self.assertEqual(progress["tokens_seen"], expected)

    def test_checkpoints_land_on_optimizer_step_boundaries(self):
        cfg = self.tiny(max_steps=3)
        train(cfg)
        for step in (1, 2, 3):
            self.assertTrue(os.path.exists(os.path.join(self.ckpt_dir, f"step_{step}.pt")))
        self.assertFalse(os.path.exists(os.path.join(self.ckpt_dir, "step_6.pt")))

    def test_clip_runs_once_per_optimizer_update_on_accumulated_grads(self):
        cfg = self.tiny()
        calls = []
        real = torch.nn.utils.clip_grad_norm_

        def spy(parameters, max_norm, *args, **kwargs):
            params = [p for p in parameters if p.grad is not None]
            norm = torch.sqrt(sum((p.grad.detach() ** 2).sum() for p in params))
            calls.append(float(norm))
            return real(params, max_norm, *args, **kwargs)

        with mock.patch("torch.nn.utils.clip_grad_norm_", spy):
            train(cfg)
        # Exactly one clip per optimizer step, and it sees a populated gradient.
        self.assertEqual(len(calls), cfg.max_steps)
        self.assertTrue(all(n > 0.0 for n in calls))

    def test_accum_one_is_the_plain_loop(self):
        cfg = self.tiny(grad_accum_steps=1)
        train(cfg)
        final = os.path.join(self.ckpt_dir, "final.pt")
        self.assertEqual(read_checkpoint(final)["step"], cfg.max_steps)
        self.assertEqual(self.adam_step(final), cfg.max_steps)

    def test_resume_continues_with_accumulation(self):
        cfg = self.tiny(max_steps=2)
        train(cfg)
        train(self.tiny(max_steps=4),
              resume_from=os.path.join(self.ckpt_dir, "step_2.pt"), start_step=3)
        final = os.path.join(self.ckpt_dir, "final.pt")
        self.assertEqual(read_checkpoint(final)["step"], 4)
        # Steps 3 and 4 update the optimizer two more times, on top of 2 already.
        self.assertEqual(self.adam_step(final), 4)


class TestGradAccumCliEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name)
        self.val_bin = os.path.join(self.tmp.name, "val.bin")
        self.tokenizer = os.path.join(self.tmp.name, "tokenizer.json")
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def run_main(self, *argv) -> None:
        saved = sys.argv
        sys.argv = ["train.py", *argv]
        try:
            train_main()
        finally:
            sys.argv = saved

    def data_flags(self) -> list[str]:
        return ["--train-bin", self.train_bin, "--val-bin", self.val_bin,
                "--tokenizer", self.tokenizer, "--checkpoint-dir", self.ckpt_dir,
                "--context-length", "16", "--max-steps", "2", "--batch-size", "2",
                "--eval-interval", "2", "--eval-batches", "2"]

    def test_flag_reaches_the_checkpoint(self):
        self.run_main(*self.data_flags(), "--grad-accum-steps", "3", "--seed", "0")
        cfg = Config.from_dict(
            read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))["config"])
        self.assertEqual(cfg.grad_accum_steps, 3)

    def test_invalid_value_is_a_clear_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                self.run_main(*self.data_flags(), "--grad-accum-steps", "0")
        self.assertIn("grad_accum_steps", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.ckpt_dir, "final.pt")))


if __name__ == "__main__":
    unittest.main()
