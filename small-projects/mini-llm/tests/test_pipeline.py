"""Pipeline tests: data prep, dataset, training loop, config/data agreement. CPU only."""

import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prepare_data
from src.config import Config, config_for_data, load_data_meta
from src.dataset import TokenDataset, build_dataloader, load_token_ids
from src.generate import generate_tokens
from src.model import from_config
from src.tokenizer import load_tokenizer
from src.train import evaluate, load_model, lr_at_step, train

CORPUS = (
    "The tiny language model reads a sequence of tokens and guesses the next one.\n\n"
    "Causal self attention lets every position look only at earlier positions.\n\n"
    "A transformer block has skip connections that add the input back.\n\n"
) * 12


def write_corpus(path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(CORPUS)
    return path


def build_data(tmp: str, context_length: int = 16, val_frac: float = 0.2) -> str:
    """Run prepare_data.main() over a temporary corpus; return the train .bin path."""
    corpus = write_corpus(os.path.join(tmp, "corpus.txt"))
    argv = sys.argv
    sys.argv = [
        "prepare_data.py",
        "--input", corpus,
        "--tokenizer-out", os.path.join(tmp, "tokenizer.json"),
        "--train-out", os.path.join(tmp, "train.bin"),
        "--val-out", os.path.join(tmp, "val.bin"),
        "--vocab-size", "512",
        "--min-frequency", "1",
        "--val-frac", str(val_frac),
        "--context-length", str(context_length),
    ]
    try:
        prepare_data.main()
    finally:
        sys.argv = argv
    return os.path.join(tmp, "train.bin")


class TestPrepareData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_writes_meta_matching_tokenizer(self):
        train_bin = build_data(self.tmp.name)
        meta = load_data_meta(train_bin)
        self.assertIsNotNone(meta)
        tok = load_tokenizer(os.path.join(self.tmp.name, "tokenizer.json"))
        self.assertEqual(meta["vocab_size"], tok.get_vocab_size())
        self.assertEqual(meta["dtype"], "uint16")
        train_ids = load_token_ids(train_bin)
        self.assertEqual(meta["train_tokens"], len(train_ids))
        self.assertLess(int(train_ids.max()), meta["vocab_size"])

    def test_special_tokens_are_trained(self):
        train_bin = build_data(self.tmp.name)
        tok = load_tokenizer(os.path.join(self.tmp.name, "tokenizer.json"))
        ids = set(load_token_ids(train_bin).tolist())
        ids |= set(load_token_ids(os.path.join(self.tmp.name, "val.bin")).tolist())
        for special in ("<bos>", "<eos>"):
            self.assertIn(tok.token_to_id(special), ids, f"{special} never trained")

    def test_val_split_covers_one_context_sample(self):
        train_bin = build_data(self.tmp.name, context_length=16, val_frac=0.001)
        val = load_token_ids(os.path.join(self.tmp.name, "val.bin"))
        self.assertGreaterEqual(len(val), 18)  # context_length + 2

    def test_too_small_corpus_is_rejected(self):
        tiny = os.path.join(self.tmp.name, "tiny.txt")
        with open(tiny, "w", encoding="utf-8") as f:
            f.write("too small\n")
        argv = sys.argv
        sys.argv = ["prepare_data.py", "--input", tiny, "--tokenizer-out",
                    os.path.join(self.tmp.name, "t.json"), "--train-out",
                    os.path.join(self.tmp.name, "t.bin"), "--val-out",
                    os.path.join(self.tmp.name, "v.bin"), "--min-frequency", "1",
                    "--context-length", "512"]
        try:
            with self.assertRaises(SystemExit):
                prepare_data.main()
        finally:
            sys.argv = argv

    def test_vocab_size_above_uint16_is_rejected(self):
        argv = sys.argv
        sys.argv = ["prepare_data.py", "--vocab-size", "70000"]
        try:
            with self.assertRaises(SystemExit):
                prepare_data.main()
        finally:
            sys.argv = argv


class TestDataset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name)

    def test_samples_are_shifted_pairs(self):
        tokens = load_token_ids(self.train_bin)
        ds = TokenDataset(tokens, context_length=16)
        x, y = ds[0]
        self.assertEqual(x.shape, (16,))
        self.assertEqual(y.shape, (16,))
        self.assertTrue(torch.equal(x, torch.from_numpy(tokens[:16].astype(np.int64))))
        self.assertTrue(torch.equal(y, torch.from_numpy(tokens[1:17].astype(np.int64))))
        self.assertEqual(x.dtype, torch.int64)

    def test_len_matches_sample_count(self):
        tokens = load_token_ids(self.train_bin)
        ds = TokenDataset(tokens, context_length=16)
        self.assertEqual(len(ds), (len(tokens) - 1) // 17)

    def test_too_few_tokens_raises_with_hint(self):
        with self.assertRaises(ValueError) as ctx:
            TokenDataset(np.arange(5), context_length=16)
        self.assertIn("context_length", str(ctx.exception))

    def test_missing_and_empty_files_raise(self):
        with self.assertRaises(FileNotFoundError):
            load_token_ids(os.path.join(self.tmp.name, "nope.bin"))
        empty = os.path.join(self.tmp.name, "empty.bin")
        open(empty, "wb").close()
        with self.assertRaises(ValueError):
            load_token_ids(empty)

    def test_train_loader_diagnoses_missing_full_batch(self):
        with self.assertRaises(ValueError) as ctx:
            build_dataloader(self.train_bin, context_length=16, batch_size=10_000)
        self.assertIn("batch_size", str(ctx.exception))

    def test_val_loader_keeps_ragged_tail(self):
        loader = build_dataloader(
            os.path.join(self.tmp.name, "val.bin"), 16, 8, shuffle=False
        )
        self.assertGreater(len(loader), 0)
        self.assertTrue(all(x.shape[1] == 16 for x, _ in loader))


class TestEvaluate(unittest.TestCase):
    def model(self):
        return from_config(Config(vocab_size=32, context_length=16, n_layers=1,
                                  n_heads=2, d_model=16, d_ff=32))

    def test_empty_loader_raises(self):
        # This used to escape as an uncaught StopIteration.
        with self.assertRaises(ValueError):
            evaluate(self.model(), DataLoader([], batch_size=8), 50, torch.device("cpu"))

    def test_does_not_cycle_batches(self):
        class CountingLoader(DataLoader):
            def __init__(self, *a, **k):
                self.epochs = 0
                super().__init__(*a, **k)

            def __iter__(self):
                self.epochs += 1
                return super().__iter__()

        loader = CountingLoader(
            TokenDataset(np.arange(17 * 6) % 32, 16), batch_size=2, shuffle=False
        )
        self.assertEqual(len(loader), 3)
        evaluate(self.model(), loader, 50, torch.device("cpu"))
        self.assertEqual(loader.epochs, 1)


class TestTrainingLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=6, warmup_steps=2, eval_interval=3,
                  eval_batches=2, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def test_warmup_starts_at_one_step_of_base(self):
        cfg = Config()
        self.assertAlmostEqual(lr_at_step(1, cfg), cfg.learning_rate / cfg.warmup_steps)
        self.assertAlmostEqual(lr_at_step(cfg.warmup_steps, cfg), cfg.learning_rate)
        self.assertAlmostEqual(
            lr_at_step(cfg.max_steps, cfg), cfg.learning_rate * cfg.min_lr_ratio
        )

    def test_train_writes_checkpoints_with_losses(self):
        cfg = self.tiny()
        train(cfg)
        final = os.path.join(self.ckpt_dir, "final.pt")
        self.assertTrue(os.path.exists(final))
        ckpt = torch.load(final, map_location="cpu", weights_only=False)
        for key in ("model_state", "optimizer_state", "scheduler_state", "step",
                    "config", "train_loss", "val_loss"):
            self.assertIn(key, ckpt)
        self.assertEqual(ckpt["step"], cfg.max_steps)
        self.assertTrue(math.isfinite(ckpt["val_loss"]))
        self.assertTrue(os.path.exists(os.path.join(self.ckpt_dir, "step_6.pt")))

    def test_checkpoint_round_trip_predicts_identically(self):
        cfg = self.tiny()
        train(cfg)
        path = os.path.join(self.ckpt_dir, "final.pt")
        loaded, loaded_cfg = load_model(path)
        fresh = from_config(cfg)
        fresh.load_state_dict(torch.load(
            path, map_location="cpu", weights_only=False)["model_state"])
        fresh.eval()
        self.assertEqual(loaded_cfg.to_dict(), cfg.to_dict())
        x = torch.randint(0, cfg.vocab_size, (1, cfg.context_length))
        with torch.no_grad():
            self.assertTrue(torch.equal(fresh(x)[0], loaded(x)[0]))

    def test_generation_from_trained_checkpoint(self):
        cfg = self.tiny()
        train(cfg)
        model, _ = load_model(os.path.join(self.ckpt_dir, "final.pt"))
        out = generate_tokens(model, torch.tensor([[2]]), n_tokens=5, temperature=1.0)
        self.assertLessEqual(out.shape[1], 6)
        self.assertTrue(((out >= 0) & (out < cfg.vocab_size)).all().item())


class TestGenerateArgs(unittest.TestCase):
    def setUp(self):
        self.model = from_config(
            Config(vocab_size=32, context_length=8, n_layers=1, n_heads=2,
                   d_model=16, d_ff=32)
        )
        self.prompt = torch.zeros(1, 4, dtype=torch.long)

    def test_rejects_negative_temperature(self):
        with self.assertRaises(ValueError):
            generate_tokens(self.model, self.prompt, 1, temperature=-1.0)

    def test_rejects_non_finite_temperature(self):
        with self.assertRaises(ValueError):
            generate_tokens(self.model, self.prompt, 1, temperature=float("inf"))

    def test_rejects_bad_top_k(self):
        for k in (0, -1):
            with self.assertRaises(ValueError):
                generate_tokens(self.model, self.prompt, 1, temperature=1.0, top_k=k)

    def test_tiny_temperature_stays_finite(self):
        out = generate_tokens(self.model, self.prompt, 3, temperature=1e-45, top_k=5)
        self.assertEqual(out.shape, (1, 7))

    def test_stops_at_eos(self):
        class AlwaysEos(torch.nn.Module):
            context_length = 8

            def __init__(self):
                super().__init__()
                self.calls = 0

            def forward(self, idx, targets=None):
                self.calls += 1
                logits = torch.zeros(idx.size(0), idx.size(1), 32)
                logits[:, :, 5] = 10.0
                return logits, None

        m = AlwaysEos()
        out = generate_tokens(m, self.prompt, n_tokens=10, temperature=0.0, eos_id=5)
        self.assertEqual(out.shape, (1, 5))
        self.assertEqual(out[0, -1].item(), 5)
        self.assertEqual(m.calls, 1)


class TestConfigDataAgreement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)

    def test_vocab_size_adopted_from_meta(self):
        meta = load_data_meta(self.train_bin)
        cfg = config_for_data(self.train_bin, n_layers=1, n_heads=2, d_model=16, d_ff=32)
        self.assertEqual(cfg.vocab_size, meta["vocab_size"])

    def test_explicit_vocab_size_must_match_data(self):
        meta = load_data_meta(self.train_bin)
        with self.assertRaises(ValueError):
            config_for_data(self.train_bin, vocab_size=meta["vocab_size"] + 1,
                            n_layers=1, n_heads=2, d_model=16, d_ff=32)

    def test_missing_meta_is_an_error(self):
        os.mkdir(os.path.join(self.tmp.name, "unprepared"))
        with self.assertRaises(FileNotFoundError):
            config_for_data(os.path.join(self.tmp.name, "unprepared", "absent.bin"))

    def test_config_round_trips_through_checkpoint_dict(self):
        cfg = Config(vocab_size=128, context_length=8, n_layers=1, n_heads=2,
                     d_model=16, d_ff=32)
        self.assertEqual(Config.from_dict(json.loads(json.dumps(cfg.to_dict()))), cfg)


if __name__ == "__main__":
    unittest.main()
