"""Stage 5: chunked language-model loss.

--loss-chunk-size recomputes each chunk of the output projection during backward,
so the full (B, T, vocab) logits tensor is never live. These tests pin loss and
gradient equivalence against the plain path, correctness with uneven chunks and
ignored targets, unchanged checkpoint/generation behavior, and input validation.
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
from src.generate import generate_tokens
from src.model import from_config
from src.train import (apply_cli_config, evaluate, load_model, make_parser,
                       read_checkpoint, train)
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


def small_cfg(**over) -> Config:
    kw = dict(vocab_size=64, context_length=16, n_layers=2, n_heads=2, d_model=32,
              d_ff=64, dropout=0.0)
    kw.update(over)
    return Config(**kw)


class TestLossChunkConfig(unittest.TestCase):
    def test_default_is_disabled(self):
        self.assertEqual(Config().loss_chunk_size, 0)

    def test_the_field_is_stored_with_the_config(self):
        self.assertIn("loss_chunk_size", Config().to_dict())

    def test_zero_is_allowed_and_negative_or_non_int_is_rejected(self):
        self.assertEqual(Config(loss_chunk_size=0).loss_chunk_size, 0)
        for bad in (-1, 1.5, "4"):
            with self.subTest(loss_chunk_size=bad):
                with self.assertRaises(AssertionError) as ctx:
                    Config(loss_chunk_size=bad)
                self.assertIn("loss_chunk_size", str(ctx.exception))


class TestLossChunkCli(unittest.TestCase):
    def test_flag_defaults_to_none(self):
        self.assertIsNone(make_parser().parse_args([]).loss_chunk_size)

    def test_flag_sets_the_field(self):
        cfg = Config()
        apply_cli_config(cfg, make_parser().parse_args(["--loss-chunk-size", "32"]))
        self.assertEqual(cfg.loss_chunk_size, 32)

    def test_bare_invocation_leaves_the_default(self):
        cfg = Config()
        before = cfg.to_dict()
        apply_cli_config(cfg, make_parser().parse_args([]))
        self.assertEqual(cfg.to_dict(), before)


class TestChunkedLossEquivalence(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(0)
        self.cfg = small_cfg()
        self.model = from_config(self.cfg)
        self.x = torch.randint(0, self.cfg.vocab_size, (2, self.cfg.context_length))
        self.y = torch.randint(0, self.cfg.vocab_size, (2, self.cfg.context_length))

    def test_loss_matches_the_plain_path_for_every_chunk_size(self):
        _, plain = self.model(self.x, self.y)
        for chunk in (1, 3, 7, 16, 100):
            with self.subTest(chunk_size=chunk):
                chunked = self.model.chunked_loss(self.x, self.y, chunk)
                self.assertTrue(torch.allclose(plain, chunked, atol=1e-6),
                                f"{plain.item()} != {chunked.item()} at chunk {chunk}")

    def test_uneven_final_chunk_matches(self):
        # T=10 with chunk 3 -> 3, 3, 3, 1: the last chunk is short.
        x = torch.randint(0, self.cfg.vocab_size, (2, 10))
        y = torch.randint(0, self.cfg.vocab_size, (2, 10))
        _, plain = self.model(x, y)
        self.assertTrue(torch.allclose(plain, self.model.chunked_loss(x, y, 3), atol=1e-6))

    def test_gradients_match_the_plain_path(self):
        _, plain = self.model(self.x, self.y)
        plain.backward()
        plain_grads = {name: p.grad.clone() for name, p in self.model.named_parameters()}

        self.model.zero_grad()
        self.model.chunked_loss(self.x, self.y, 5).backward()
        for name, p in self.model.named_parameters():
            self.assertTrue(
                torch.allclose(plain_grads[name], p.grad, atol=1e-6),
                f"gradient mismatch on {name}",
            )

    def test_ignored_targets_match_the_plain_path(self):
        """ignore_index (-100) tokens are excluded by both paths."""
        y = self.y.clone()
        y[0, :4] = -100
        y[1, 8] = -100
        _, plain = self.model(self.x, y)
        for chunk in (2, 5, 16):
            with self.subTest(chunk_size=chunk):
                chunked = self.model.chunked_loss(self.x, y, chunk)
                self.assertTrue(torch.allclose(plain, chunked, atol=1e-6),
                                f"{plain.item()} != {chunked.item()} at chunk {chunk}")

    def test_direct_call_rejects_a_non_positive_chunk(self):
        with self.assertRaises(ValueError):
            self.model.chunked_loss(self.x, self.y, 0)

    def test_hidden_states_feed_the_same_logits(self):
        with torch.no_grad():
            logits, _ = self.model(self.x)
            restored = self.model.lm_head(self.model.hidden_states(self.x))
        self.assertTrue(torch.equal(logits, restored))


class TestChunkedTrainingLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=3, warmup_steps=1, eval_interval=3,
                  eval_batches=1, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def test_training_with_chunked_loss_writes_finite_checkpoints(self):
        cfg = self.tiny(loss_chunk_size=4)
        train(cfg)
        ckpt = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        self.assertEqual(ckpt["step"], cfg.max_steps)
        self.assertEqual(ckpt["config"]["loss_chunk_size"], 4)
        self.assertTrue(torch.isfinite(torch.tensor(ckpt["train_loss"])))
        self.assertTrue(torch.isfinite(torch.tensor(ckpt["val_loss"])))

    def test_chunked_and_plain_training_agree(self):
        plain_dir = os.path.join(self.tmp.name, "plain")
        chunk_dir = os.path.join(self.tmp.name, "chunk")
        train(self.tiny(checkpoint_dir=plain_dir))
        train(self.tiny(loss_chunk_size=4, checkpoint_dir=chunk_dir))
        plain = read_checkpoint(os.path.join(plain_dir, "final.pt"))["model_state"]
        chunk = read_checkpoint(os.path.join(chunk_dir, "final.pt"))["model_state"]
        for name, tensor in plain.items():
            self.assertTrue(torch.allclose(tensor, chunk[name], atol=1e-4, rtol=1e-4),
                            f"weights diverged on {name}")

    def test_evaluation_chunk_size_matches_plain(self):
        from src.dataset import build_dataloader

        cfg = self.tiny()
        model = from_config(cfg)
        loader = build_dataloader(os.path.join(self.tmp.name, "val.bin"),
                                  16, 2, shuffle=False)
        device = torch.device("cpu")
        plain = evaluate(model, loader, 1, device, 0)
        for chunk in (4, 7):
            with self.subTest(chunk_size=chunk):
                self.assertAlmostEqual(evaluate(model, loader, 1, device, chunk), plain,
                                       places=5)

    def test_checkpoint_loads_and_generates_after_chunked_training(self):
        cfg = self.tiny(loss_chunk_size=5)
        train(cfg)
        path = os.path.join(self.ckpt_dir, "final.pt")
        model, loaded_cfg = load_model(path)
        self.assertEqual(loaded_cfg.loss_chunk_size, 5)
        logits, loss = model(torch.randint(0, cfg.vocab_size, (1, 8)),
                             torch.randint(0, cfg.vocab_size, (1, 8)))
        self.assertEqual(logits.shape, (1, 8, cfg.vocab_size))
        self.assertTrue(torch.isfinite(loss))
        out = generate_tokens(model, torch.tensor([[2]]), n_tokens=5, temperature=1.0)
        self.assertTrue(((out >= 0) & (out < cfg.vocab_size)).all().item())


class TestLossChunkCliEndToEnd(unittest.TestCase):
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
        self.run_main(*self.data_flags(), "--loss-chunk-size", "8", "--seed", "0")
        cfg = Config.from_dict(
            read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))["config"])
        self.assertEqual(cfg.loss_chunk_size, 8)

    def test_invalid_value_is_a_clear_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                self.run_main(*self.data_flags(), "--loss-chunk-size", "-1")
        self.assertIn("loss_chunk_size", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.ckpt_dir, "final.pt")))


if __name__ == "__main__":
    unittest.main()
