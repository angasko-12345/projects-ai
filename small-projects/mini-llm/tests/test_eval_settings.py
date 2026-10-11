"""Evaluation settings must be positive integers, everywhere they are set.

Before this fix Config never validated eval_interval or eval_batches, so
--eval-batches 0 made evaluate() report 0.0 without reading a single batch
and that fake loss went into every checkpoint, while --eval-interval 0
crashed the training loop on step % 0. These tests pin the three layers
that must refuse such values: direct Config construction, the CLI before
any training or checkpoint, and evaluate() itself as the last line of
defense.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

import numpy as np
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prepare_data
from src.config import Config
from src.dataset import TokenDataset
from src.model import from_config
from src.train import evaluate, read_checkpoint
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


class TestConfigRequiresPositiveEvalSettings(unittest.TestCase):
    def test_zero_and_negative_eval_interval_are_rejected(self):
        for bad in (0, -1):
            with self.subTest(eval_interval=bad):
                with self.assertRaises(AssertionError) as ctx:
                    Config(eval_interval=bad)
                self.assertIn("eval_interval", str(ctx.exception))

    def test_zero_and_negative_eval_batches_are_rejected(self):
        for bad in (0, -1):
            with self.subTest(eval_batches=bad):
                with self.assertRaises(AssertionError) as ctx:
                    Config(eval_batches=bad)
                self.assertIn("eval_batches", str(ctx.exception))

    def test_boundary_values_and_defaults_still_construct(self):
        cfg = Config(eval_interval=1, eval_batches=1)
        self.assertEqual((cfg.eval_interval, cfg.eval_batches), (1, 1))
        defaults = Config()
        self.assertEqual((defaults.eval_interval, defaults.eval_batches), (500, 50))


class TestCliRefusesInvalidEvalSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def run_main(self, *argv) -> None:
        saved = sys.argv
        sys.argv = ["train.py", *argv]
        try:
            train_main()
        finally:
            sys.argv = saved

    def data_flags(self) -> list[str]:
        return ["--train-bin", self.train_bin,
                "--val-bin", os.path.join(self.tmp.name, "val.bin"),
                "--tokenizer", os.path.join(self.tmp.name, "tokenizer.json"),
                "--checkpoint-dir", self.ckpt_dir,
                "--context-length", "16", "--max-steps", "2", "--batch-size", "2"]

    def test_zero_values_are_usage_errors_before_any_checkpoint(self):
        for flag, field in (("--eval-interval", "eval_interval"),
                            ("--eval-batches", "eval_batches")):
            with self.subTest(flag=flag):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as ctx:
                        self.run_main(*self.data_flags(), flag, "0")
                self.assertEqual(ctx.exception.code, 2)  # argparse usage error
                message = err.getvalue()
                self.assertIn("usage:", message)
                self.assertIn(field, message)
                self.assertFalse(os.path.exists(self.ckpt_dir))

    def test_negative_eval_interval_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                self.run_main(*self.data_flags(), "--eval-interval", "-1")
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("eval_interval", err.getvalue())

    def test_positive_values_still_train(self):
        self.run_main(*self.data_flags(), "--eval-interval", "1", "--eval-batches", "1",
                      "--n-layers", "1", "--n-heads", "2", "--d-model", "16",
                      "--d-ff", "32", "--seed", "0")
        ckpt = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        cfg = Config.from_dict(ckpt["config"])
        self.assertEqual((cfg.eval_interval, cfg.eval_batches), (1, 1))
        # A real evaluation happened: the stored loss is measured, not a zero-batch zero.
        self.assertGreater(ckpt["val_loss"], 0.0)


class TestEvaluateRefusesNonPositiveLimit(unittest.TestCase):
    def model(self):
        return from_config(Config(vocab_size=32, context_length=16, n_layers=1,
                                  n_heads=2, d_model=16, d_ff=32))

    def loader(self):
        return DataLoader(TokenDataset(np.arange(17 * 6) % 32, 16), batch_size=2,
                          shuffle=False)

    def test_zero_limit_raises_instead_of_returning_a_fake_zero(self):
        with self.assertRaises(ValueError) as ctx:
            evaluate(self.model(), self.loader(), 0, torch.device("cpu"))
        self.assertIn("batches", str(ctx.exception))

    def test_negative_limit_raises(self):
        with self.assertRaises(ValueError) as ctx:
            evaluate(self.model(), self.loader(), -1, torch.device("cpu"))
        self.assertIn("batches", str(ctx.exception))

    def test_positive_limit_still_returns_a_real_loss(self):
        loss = evaluate(self.model(), self.loader(), 2, torch.device("cpu"))
        self.assertGreater(loss, 0.0)


if __name__ == "__main__":
    unittest.main()
