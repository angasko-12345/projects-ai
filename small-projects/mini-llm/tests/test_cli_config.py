"""Stage 1: choose the model size and training hyperparameters from the CLI.

Config has long carried the full architecture and optimizer surface, but
make_parser exposed almost none of it, so the only way to train anything other
than the default shape was to edit config.py. These tests pin the CLI: every
field is reachable by flag, a bare invocation still yields the library defaults,
a non-default shape builds and trains, and an impossible combination is refused
with a readable message instead of a traceback.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prepare_data
from src.config import Config, load_data_meta
from src.model import from_config
from src.train import apply_cli_config, make_parser, read_checkpoint
from src.train import main as train_main

# The flags this task adds, keyed by argument dest. tokenizer is included: the
# Config field is tokenizer_path and --tokenizer-path is the same flag.
NEW_FLAGS = (
    "n_layers", "n_heads", "d_model", "d_ff", "learning_rate", "dropout",
    "grad_clip", "eval_interval", "eval_batches", "warmup_steps", "seed",
    "weight_decay", "min_lr_ratio", "beta1", "beta2", "tokenizer",
)

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


class TestCliDefaults(unittest.TestCase):
    def test_new_flags_default_to_none(self):
        """None is what makes an unset flag unable to change a Config default."""
        args = make_parser().parse_args([])
        for dest in NEW_FLAGS:
            self.assertIsNone(getattr(args, dest), dest)

    def test_bare_invocation_leaves_the_config_untouched(self):
        cfg = Config()
        before = cfg.to_dict()
        apply_cli_config(cfg, make_parser().parse_args([]))
        self.assertEqual(cfg.to_dict(), before)

    def test_library_defaults_are_unchanged(self):
        cfg = Config()
        self.assertEqual((cfg.n_layers, cfg.n_heads, cfg.d_model, cfg.d_ff, cfg.dropout),
                         (6, 6, 384, 1536, 0.0))
        self.assertEqual((cfg.learning_rate, cfg.weight_decay, cfg.min_lr_ratio),
                         (3e-4, 0.1, 0.1))
        self.assertEqual((cfg.beta1, cfg.beta2), (0.9, 0.95))
        self.assertEqual((cfg.grad_clip, cfg.eval_interval, cfg.eval_batches,
                          cfg.warmup_steps, cfg.seed), (1.0, 500, 50, 500, 42))
        self.assertEqual(cfg.tokenizer_path, "data/tokenizer.json")


class TestCliOverrides(unittest.TestCase):
    def test_each_flag_updates_its_field(self):
        args = make_parser().parse_args([
            "--n-layers", "3", "--n-heads", "4", "--d-model", "64", "--d-ff", "128",
            "--dropout", "0.2", "--learning-rate", "0.001", "--weight-decay", "0.2",
            "--min-lr-ratio", "0.05", "--beta1", "0.8", "--beta2", "0.99",
            "--grad-clip", "0.5", "--warmup-steps", "4", "--eval-interval", "7",
            "--eval-batches", "9", "--seed", "1234",
            "--tokenizer-path", "toks/custom.json",
        ])
        cfg = Config(context_length=16, max_steps=10, warmup_steps=2)
        apply_cli_config(cfg, args)
        self.assertEqual((cfg.n_layers, cfg.n_heads, cfg.d_model, cfg.d_ff),
                         (3, 4, 64, 128))
        self.assertEqual((cfg.learning_rate, cfg.weight_decay, cfg.min_lr_ratio),
                         (0.001, 0.2, 0.05))
        self.assertEqual((cfg.beta1, cfg.beta2, cfg.grad_clip), (0.8, 0.99, 0.5))
        self.assertEqual((cfg.warmup_steps, cfg.eval_interval, cfg.eval_batches),
                         (4, 7, 9))
        self.assertEqual(cfg.dropout, 0.2)
        self.assertEqual(cfg.seed, 1234)
        self.assertEqual(cfg.tokenizer_path, "toks/custom.json")

    def test_only_the_passed_flag_moves(self):
        cfg = Config(n_layers=3, n_heads=4, d_model=64, d_ff=128, seed=7)
        before = cfg.to_dict()
        apply_cli_config(cfg, make_parser().parse_args(["--max-steps", "5"]))
        after = cfg.to_dict()
        self.assertEqual({k for k in before if before[k] != after[k]}, {"max_steps"})

    def test_tokenizer_and_tokenizer_path_are_one_flag(self):
        self.assertEqual(
            make_parser().parse_args(["--tokenizer", "a.json"]).tokenizer, "a.json")
        self.assertEqual(
            make_parser().parse_args(["--tokenizer-path", "b.json"]).tokenizer, "b.json")


class TestNonDefaultModel(unittest.TestCase):
    def test_non_default_shape_constructs(self):
        cfg = Config(vocab_size=2048, context_length=128, n_layers=4, n_heads=8,
                     d_model=256, d_ff=1024, dropout=0.1)
        model = from_config(cfg)
        self.assertEqual(model.count_parameters(), 3_713_536)
        x = torch.randint(0, cfg.vocab_size, (2, cfg.context_length))
        logits, loss = model(x, x)
        self.assertEqual(logits.shape, (2, cfg.context_length, cfg.vocab_size))
        self.assertTrue(torch.isfinite(loss).item())


class TestInvalidConfig(unittest.TestCase):
    def test_shape_that_does_not_divide_is_rejected(self):
        with self.assertRaises(AssertionError) as ctx:
            Config(d_model=100, n_heads=3)
        self.assertIn("divisible", str(ctx.exception))

    def test_zero_heads_is_rejected_not_a_zero_division(self):
        with self.assertRaises(AssertionError) as ctx:
            Config(n_heads=0)
        self.assertIn("n_heads", str(ctx.exception))

    def test_non_positive_dimensions_are_rejected(self):
        for field in ("n_layers", "d_model", "d_ff"):
            with self.subTest(field=field):
                with self.assertRaises(AssertionError) as ctx:
                    Config(**{field: 0})
                self.assertIn(field, str(ctx.exception))


class TestCliEndToEnd(unittest.TestCase):
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

    def stored_config(self):
        return Config.from_dict(
            read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))["config"])

    def test_selected_model_size_reaches_the_checkpoint(self):
        self.run_main(*self.data_flags(), "--n-layers", "3", "--n-heads", "4",
                      "--d-model", "64", "--d-ff", "128", "--seed", "0")
        cfg = self.stored_config()
        self.assertEqual((cfg.n_layers, cfg.n_heads, cfg.d_model, cfg.d_ff),
                         (3, 4, 64, 128))
        # The shape is real: the saved config rebuilds a model of that shape at
        # the vocabulary the prepared data actually reached.
        model = from_config(cfg)
        self.assertEqual(len(model.blocks), 3)
        self.assertEqual(model.wte.embedding_dim, 64)
        self.assertEqual(model.context_length, 16)

    def test_selected_hyperparameters_reach_the_checkpoint(self):
        self.run_main(*self.data_flags(), "--learning-rate", "0.0007",
                      "--weight-decay", "0.05", "--min-lr-ratio", "0.2",
                      "--beta1", "0.85", "--beta2", "0.9", "--grad-clip", "0.3",
                      "--warmup-steps", "1", "--dropout", "0.1", "--seed", "99")
        cfg = self.stored_config()
        self.assertEqual(cfg.learning_rate, 0.0007)
        self.assertEqual(cfg.weight_decay, 0.05)
        self.assertEqual(cfg.min_lr_ratio, 0.2)
        self.assertEqual((cfg.beta1, cfg.beta2), (0.85, 0.9))
        self.assertEqual(cfg.grad_clip, 0.3)
        self.assertEqual((cfg.warmup_steps, cfg.eval_interval, cfg.eval_batches),
                         (1, 2, 2))
        self.assertEqual(cfg.dropout, 0.1)
        self.assertEqual(cfg.seed, 99)

    def test_invalid_shape_is_a_clear_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                self.run_main(*self.data_flags(), "--d-model", "100", "--n-heads", "3")
        self.assertIn("divisible", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.ckpt_dir, "final.pt")))


if __name__ == "__main__":
    unittest.main()
