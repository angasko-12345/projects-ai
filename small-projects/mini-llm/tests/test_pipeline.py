"""Pipeline tests: data prep, dataset, training loop, config/data agreement. CPU only."""

import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prepare_data
from src.config import Config, config_for_data, file_sha256, load_data_meta
from src.dataset import TokenDataset, build_dataloader, load_token_ids
from src.generate import check_tokenizer_provenance
from src.generate import generate_tokens
from src.generate import main as generate_main
from src.model import from_config
from src.tokenizer import load_tokenizer, train_bpe_tokenizer
from src.train import (build_checkpoint_metadata, build_param_groups, evaluate, load_checkpoint,
                       load_model, lr_at_step, read_checkpoint, resolve_device, restore_rng_state,
                       save_checkpoint, train, validate_checkpoint, CHECKPOINT_SCHEMA_VERSION,
                       VALIDATED_KEY)
from src.train import main as train_main

CORPUS = (
    "The tiny language model reads a sequence of tokens and guesses the next one.\n\n"
    "Causal self attention lets every position look only at earlier positions.\n\n"
    "A transformer block has skip connections that add the input back.\n\n"
) * 12


def write_corpus(path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(CORPUS)
    return path


# A second corpus that shares no wording with CORPUS, so a data set built from it
# differs from a CORPUS data set in every artifact, tokenizer included.
OTHER_CORPUS = (
    "Rain drummed on the tin roof while the kettle whistled downstairs.\n\n"
    "Mira counted seven grey geese crossing the flooded meadow.\n\n"
    "Every rung of the ladder wobbled, so he tied the rope twice.\n\n"
) * 12


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


def build_data_set(root: str, vocab_size: int = 512, context_length: int = 16,
                   val_frac: float = 0.2, corpus_text: str = CORPUS) -> dict:
    """Prepare a data set under `root` and return the paths it produced.

    Same contract as build_data, but named rather than fixed to one temp dir, so
    a test can hold two independent data sets (and their tokenizers) side by side.
    """
    os.makedirs(root, exist_ok=True)
    corpus = os.path.join(root, "corpus.txt")
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(corpus_text)
    argv = sys.argv
    sys.argv = [
        "prepare_data.py",
        "--input", corpus,
        "--tokenizer-out", os.path.join(root, "tokenizer.json"),
        "--train-out", os.path.join(root, "train.bin"),
        "--val-out", os.path.join(root, "val.bin"),
        "--vocab-size", str(vocab_size),
        "--min-frequency", "1",
        "--val-frac", str(val_frac),
        "--context-length", str(context_length),
    ]
    try:
        prepare_data.main()
    finally:
        sys.argv = argv
    return {
        "root": root,
        "train_bin": os.path.join(root, "train.bin"),
        "val_bin": os.path.join(root, "val.bin"),
        "tokenizer": os.path.join(root, "tokenizer.json"),
    }


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
        self.assertEqual(len(ds), len(tokens) - 16)

    def test_sliding_window_supervises_every_transition(self):
        """stride 1 must not drop the transitions that straddle window seams."""
        tokens = np.arange(100)
        ds = TokenDataset(tokens, context_length=8, stride=1)
        self.assertEqual(len(ds), 92)
        covered = set()
        for i in range(len(ds)):
            start = i * ds.stride
            covered.update(range(start + 1, start + ds.seq_len))
        self.assertEqual(covered, set(range(1, 100)))

    def test_non_overlapping_stride_still_supported(self):
        ds = TokenDataset(np.arange(100), context_length=8, stride=9)
        self.assertEqual(len(ds), (100 - 9) // 9 + 1)
        self.assertEqual(ds[1][0][0].item(), 9)

    def test_rejects_bad_stride(self):
        with self.assertRaises(ValueError):
            TokenDataset(np.arange(100), context_length=8, stride=0)

    def test_sample_counts_by_stride(self):
        """The documented stride table: samples shrink ~linearly with stride."""
        tokens = np.arange(736)
        counts = {s: len(TokenDataset(tokens, context_length=32, stride=s))
                  for s in (1, 32, 33)}
        self.assertEqual(counts[1], 704)   # 736 - 32
        self.assertEqual(counts[32], 22)
        self.assertEqual(counts[33], 22)   # context and context+1 both tile
        self.assertGreater(counts[1], 30 * counts[32])  # ~33x more windows

    def test_loader_passes_stride_through(self):
        path = os.path.join(self.tmp.name, "train.bin")
        n = len(load_token_ids(path))
        sliding = build_dataloader(path, 16, 2, shuffle=False)              # stride 1
        chunked = build_dataloader(path, 16, 2, shuffle=False, stride=16)   # non-overlapping
        self.assertEqual(len(sliding), (n - 16) // 2)
        self.assertEqual(len(chunked), ((n - 17) // 16 + 1) // 2)
        self.assertGreater(len(sliding), 10 * len(chunked))

    def test_undersized_message_reports_stride(self):
        with self.assertRaises(ValueError) as ctx:
            build_dataloader(self.train_bin, context_length=16, batch_size=10_000,
                             stride=16)
        self.assertIn("stride 16", str(ctx.exception))

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
        self.assertEqual(len(loader), (102 - 16) // 2)  # stride 1 => 43 batches
        self.assertGreater(len(loader), 3)
        evaluate(self.model(), loader, 50, torch.device("cpu"))
        self.assertEqual(loader.epochs, 1)

    def test_ragged_final_batch_is_token_weighted(self):
        """A short final batch must not count the same as a full one."""

        class PerBatchLoss(torch.nn.Module):
            """Loss varies per batch but is a plain mean over that batch's tokens."""

            def eval(self):
                return self

            def train(self):
                return self

            def forward(self, x, y):
                return None, x.float().mean()

        tokens = torch.arange(1, 22)  # 21 tokens -> 5 samples of x=4, y=4
        ds = TokenDatasetList([(tokens[i : i + 4], tokens[i + 1 : i + 5])
                               for i in range(0, 20, 4)])
        loader = DataLoader(ds, batch_size=3, shuffle=False, drop_last=False)
        batches = [x for x, _ in loader]
        self.assertEqual([b.shape[0] for b in batches], [3, 2])  # ragged tail

        per_batch = [b.float().mean().item() for b in batches]
        tokens_per_batch = [b.numel() for b in batches]
        expected = sum(l * n for l, n in zip(per_batch, tokens_per_batch)) / sum(tokens_per_batch)
        naive = sum(per_batch) / len(per_batch)

        self.assertAlmostEqual(
            evaluate(PerBatchLoss(), loader, 50, torch.device("cpu")), expected, places=5
        )
        self.assertNotAlmostEqual(expected, naive, places=3)  # the bug this guards


class TokenDatasetList(torch.utils.data.Dataset):
    def __init__(self, pairs):
        self.pairs = pairs

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, i):
        return self.pairs[i]


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
                  tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
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
        ckpt = read_checkpoint(final)
        for key in ("model_state", "optimizer_state", "scheduler_state", "step",
                    "config", "rng_state", "train_loss", "val_loss"):
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
        fresh.load_state_dict(read_checkpoint(path)["model_state"])
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


class TestResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=9, warmup_steps=3, eval_interval=3,
                  eval_batches=2, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def test_resume_restores_step_optimizer_and_schedule(self):
        cfg = self.tiny(max_steps=3)
        train(cfg)
        mid = read_checkpoint(os.path.join(self.ckpt_dir, "step_3.pt"))
        self.assertEqual(mid["step"], 3)

        # A fresh run must not equal the resumed one, or the test proves nothing.
        fresh_cfg = self.tiny(max_steps=3, checkpoint_dir=os.path.join(self.tmp.name, "fresh"))
        train(fresh_cfg)

        train(self.tiny(), resume_from=os.path.join(self.ckpt_dir, "step_3.pt"),
              start_step=4)
        done = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        self.assertEqual(done["step"], 9)

        # Optimizer moments carried over: exp_avg is populated and not the fresh
        # run's, i.e. momentum from steps 1-3 is still there.
        first_key = next(iter(done["optimizer_state"]["state"]))
        resumed_moments = done["optimizer_state"]["state"][first_key]["exp_avg"]
        self.assertIsNotNone(resumed_moments)
        self.assertGreater(float(resumed_moments.abs().sum()), 0.0)
        # The AdamW step counter is an integer tensor per parameter.
        adam_step = done["optimizer_state"]["state"][first_key]["step"]
        self.assertGreaterEqual(int(adam_step), 9)

    def test_resume_continues_the_lr_schedule(self):
        cfg = self.tiny()
        train(cfg)
        mid = read_checkpoint(os.path.join(self.ckpt_dir, "step_3.pt"))
        # Schedule state is carried, and the next step uses it rather than step 1.
        self.assertEqual(mid["scheduler_state"]["warmup_steps"], 3)
        self.assertEqual(mid["scheduler_state"]["max_steps"], 9)
        self.assertAlmostEqual(mid["scheduler_state"]["lr"], lr_at_step(3, cfg))
        self.assertNotAlmostEqual(lr_at_step(3, cfg), lr_at_step(1, cfg))
        self.assertAlmostEqual(lr_at_step(3, cfg), lr_at_step(3, cfg))
        # A restart would put step 4 back at base/warmup; the original curve is
        # already on its cosine leg and well past that value.
        self.assertGreater(lr_at_step(4, cfg), lr_at_step(1, cfg))
        self.assertLess(lr_at_step(4, cfg), lr_at_step(3, cfg))  # decaying
        self.assertAlmostEqual(
            lr_at_step(cfg.max_steps, cfg), cfg.learning_rate * cfg.min_lr_ratio)

    def test_resume_restores_rng_state(self):
        cfg = self.tiny(max_steps=3)
        train(cfg)
        path = os.path.join(self.ckpt_dir, "step_3.pt")
        expected = torch.rand(4)
        restore_rng_state(read_checkpoint(path)["rng_state"])
        self.assertTrue(torch.equal(torch.rand(4), expected))

    def test_resume_past_max_steps_is_refused(self):
        cfg = self.tiny(max_steps=3)
        train(cfg)
        with self.assertRaises(SystemExit):
            train(self.tiny(), resume_from=os.path.join(self.ckpt_dir, "step_3.pt"),
                  start_step=99)

    def test_resume_cli_restores_step(self):
        train(self.tiny())  # runs to step 9, leaving step_3.pt behind
        argv = sys.argv
        sys.argv = ["train.py", "--resume", os.path.join(self.ckpt_dir, "step_3.pt")]
        try:
            train_main()
        finally:
            sys.argv = argv
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 9)

    def test_cli_can_extend_max_steps(self):
        train(self.tiny())
        argv = sys.argv
        sys.argv = ["train.py", "--resume", os.path.join(self.ckpt_dir, "step_3.pt"),
                    "--max-steps", "12"]
        try:
            train_main()
        finally:
            sys.argv = argv
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 12)


class TestDeviceSelection(unittest.TestCase):
    def test_auto_matches_cuda_availability(self):
        expected = "cuda" if torch.cuda.is_available() else "cpu"
        self.assertEqual(resolve_device("auto").type, expected)

    def test_cpu_is_forced_even_when_cuda_exists(self):
        self.assertEqual(resolve_device("cpu").type, "cpu")

    def test_unknown_device_is_rejected(self):
        with self.assertRaises(SystemExit):
            resolve_device("tpu")

    @unittest.skipIf(torch.cuda.is_available(), "needs a CUDA-less environment")
    def test_cuda_without_cuda_fails_instead_of_falling_back(self):
        with self.assertRaises(SystemExit) as ctx:
            resolve_device("cuda")
        self.assertIn("CUDA is not available", str(ctx.exception))

    @unittest.skipUnless(torch.cuda.is_available(), "needs a CUDA device")
    def test_cuda_resolves_when_available(self):
        self.assertEqual(resolve_device("cuda").type, "cuda")


class TestParamGroups(unittest.TestCase):
    def test_one_dimensional_params_are_excluded_from_decay(self):
        cfg = Config(vocab_size=32, context_length=8, n_layers=2, n_heads=2,
                     d_model=16, d_ff=32, weight_decay=0.1)
        model = from_config(cfg)
        groups = build_param_groups(model, cfg.weight_decay)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["weight_decay"], 0.1)
        self.assertEqual(groups[1]["weight_decay"], 0.0)
        for p in groups[0]["params"]:
            self.assertGreaterEqual(p.dim(), 2)
        for p in groups[1]["params"]:
            self.assertEqual(p.dim(), 1)
        # Every trainable parameter appears exactly once, and the tied weight
        # is not counted twice.
        grouped = [id(p) for g in groups for p in g["params"]]
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(len(grouped), len(list(model.parameters())))

    def test_training_uses_the_groups(self):
        self.assertTrue(any("weight_decay" in g for g in build_param_groups(
            from_config(Config(vocab_size=32, context_length=8, n_layers=1, n_heads=2,
                                d_model=16, d_ff=32)), 0.1)))


class TestCheckpointSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tiny(self) -> Config:
        return Config(vocab_size=40, context_length=8, n_layers=1, n_heads=2,
                      d_model=16, d_ff=32)

    def test_full_checkpoint_loads_with_weights_only(self):
        cfg = self.tiny()
        model = from_config(cfg)
        opt = torch.optim.AdamW(build_param_groups(model, cfg.weight_decay), lr=1e-3)
        x = torch.randint(0, cfg.vocab_size, (2, 8))
        model(x, torch.randint(0, cfg.vocab_size, (2, 8)))[1].backward()
        opt.step()
        path = os.path.join(self.tmp.name, "ckpt.pt")
        save_checkpoint(path, model, opt, step=1, cfg=cfg, lr=1e-3,
                        extra={"train_loss": 1.0, "val_loss": 1.0})
        # No weights_only=False anywhere: this is the same call the library makes.
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        for key in ("model_state", "optimizer_state", "scheduler_state", "step",
                    "config", "rng_state", "train_loss", "val_loss"):
            self.assertIn(key, ckpt)
        # And it restores into a real optimizer, which needs no custom classes.
        fresh = from_config(cfg)
        fresh_opt = torch.optim.AdamW(build_param_groups(fresh, cfg.weight_decay), lr=1e-3)
        load_checkpoint(path, fresh, fresh_opt)
        self.assertEqual(len(fresh_opt.state_dict()["state"]),
                         len(ckpt["optimizer_state"]["state"]))

    def test_arbitrary_objects_are_rejected(self):
        path = os.path.join(self.tmp.name, "evil.pt")

        class Exploit:
            def __reduce__(self):
                return (os.system, ("echo pwned",))

        torch.save({"payload": Exploit()}, path)
        with self.assertRaises(Exception) as ctx:
            read_checkpoint(path)
        self.assertIn("not a loadable mini-llm checkpoint", str(ctx.exception))

    def test_read_checkpoint_reports_bad_files(self):
        with self.assertRaises(ValueError):
            read_checkpoint(os.path.join(self.tmp.name, "missing.pt"))

    def test_cpu_checkpoint_carries_no_cuda_rng_state(self):
        cfg = self.tiny()
        model = from_config(cfg)
        opt = torch.optim.AdamW(build_param_groups(model, cfg.weight_decay), lr=1e-3)
        path = os.path.join(self.tmp.name, "ckpt.pt")
        save_checkpoint(path, model, opt, step=1, cfg=cfg, lr=1e-3)
        rng = read_checkpoint(path)["rng_state"]
        # CPU runs keep the pre-CUDA checkpoint shape, so nothing new appears
        # for existing tooling or older readers.
        self.assertNotIn("cuda", rng)
        for key in ("torch", "python", "numpy"):
            self.assertIn(key, rng)

    @unittest.skipIf(torch.cuda.is_available(), "needs the CUDA-absent skip path")
    def test_restore_skips_cuda_states_on_cpu_only_machine(self):
        # A GPU-trained checkpoint resumed on a CPU box: the cuda states must be
        # ignored while the CPU RNG state still restores exactly.
        state = {"torch": torch.get_rng_state(),
                 "cuda": [torch.zeros(64, dtype=torch.uint8)]}
        expected = torch.rand(4)
        restore_rng_state(state)
        self.assertTrue(torch.equal(torch.rand(4), expected))


class TestCheckpointMetadata(unittest.TestCase):
    """A checkpoint says what it is: format version, model shape, tokenizer, progress.

    The gap these guard: `config` recorded the settings but nothing named the
    format or the fields it needed, so a reader had to assume both. A checkpoint
    missing a field was then completed from Config defaults - a defaulted
    vocab_size or context_length describes a different model than the weights
    belong to, and a defaulted tokenizer decodes nothing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = build_data_set(os.path.join(self.tmp.name, "set"))
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def cfg(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.data["train_bin"])["vocab_size"],
                  context_length=16, n_layers=1, n_heads=2, d_model=16, d_ff=32,
                  dropout=0.1, batch_size=2, max_steps=9, warmup_steps=2,
                  eval_interval=3, eval_batches=2, train_bin=self.data["train_bin"],
                  val_bin=self.data["val_bin"], tokenizer_path=self.data["tokenizer"],
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def save(self, cfg: Config, *, step: int = 1, progress=None, name: str | None = None) -> str:
        """Save a checkpoint the way the loop does, without training; return its path."""
        path = os.path.join(self.tmp.name, name or f"ckpt_{step}.pt")
        model = from_config(cfg)
        opt = torch.optim.AdamW(build_param_groups(model, cfg.weight_decay), lr=1e-3)
        save_checkpoint(path, model, opt, step=step, cfg=cfg, lr=1e-3, progress=progress)
        return path

    def rewrite(self, path: str, mutate) -> None:
        """Mutate a saved checkpoint and write it back, changing nothing else."""
        ckpt = read_checkpoint(path)
        mutate(ckpt)
        torch.save(ckpt, path)

    # --- saving records the metadata ---------------------------------------

    def test_save_writes_self_describing_metadata(self):
        cfg = self.cfg()
        meta = read_checkpoint(self.save(cfg, step=4))["checkpoint_metadata"]
        self.assertEqual(meta["schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(meta["progress"]["step"], 4)
        self.assertEqual(meta["progress"]["max_steps"], cfg.max_steps)
        for name in ("vocab_size", "context_length", "n_layers", "n_heads",
                     "d_model", "d_ff", "dropout"):
            self.assertEqual(meta["model"][name], getattr(cfg, name), name)
        self.assertEqual(meta["tokenizer"]["path"], self.data["tokenizer"])
        self.assertEqual(sorted(meta["required_keys"]),
                         ["config", "model_state", "step"])

    def test_metadata_loads_under_weights_only(self):
        """Plain containers only: adding the block must not cost safe loading."""
        path = self.save(self.cfg())
        meta = torch.load(path, map_location="cpu",
                          weights_only=True)["checkpoint_metadata"]
        self.assertEqual(meta["schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(meta["tokenizer"]["path"], self.data["tokenizer"])

    # --- loading restores it -------------------------------------------------

    def test_metadata_survives_a_save_load_round_trip(self):
        cfg = self.cfg()
        path = self.save(cfg, step=4, progress={"epoch": 1.5, "tokens_seen": 96})
        ckpt = read_checkpoint(path)
        self.assertEqual(ckpt["checkpoint_metadata"],
                         build_checkpoint_metadata(cfg, 4, epoch=1.5, tokens_seen=96,
                                                   provenance=ckpt["data_provenance"]))

    def test_the_recorded_model_is_the_model_that_loads(self):
        """The block describes the model that actually comes back out of the file."""
        cfg = self.cfg(n_layers=2, n_heads=4, d_model=32, d_ff=64, dropout=0.25)
        path = self.save(cfg)
        model, loaded_cfg = load_model(path)
        meta = read_checkpoint(path)["checkpoint_metadata"]
        for name in ("vocab_size", "context_length", "n_layers", "n_heads",
                     "d_model", "d_ff", "dropout"):
            self.assertEqual(meta["model"][name], getattr(loaded_cfg, name), name)
        self.assertEqual(model.context_length, cfg.context_length)
        self.assertEqual(loaded_cfg.dropout, cfg.dropout)
        self.assertEqual(model.count_parameters(),
                         from_config(loaded_cfg).count_parameters())

    # --- tokenizer information ----------------------------------------------

    def test_tokenizer_identity_survives_save_and_load(self):
        path = self.save(self.cfg())
        tokenizer = read_checkpoint(path)["checkpoint_metadata"]["tokenizer"]
        self.assertEqual(tokenizer["path"], self.data["tokenizer"])
        self.assertEqual(tokenizer["sha256"], file_sha256(self.data["tokenizer"]))
        # The digest is the identity: replacing the file at that path does not
        # change what the checkpoint says the run was trained with, so a foreign
        # tokenizer cannot pass as this one by sitting at the same path.
        with open(self.data["tokenizer"], "a", encoding="utf-8") as f:
            f.write(" ")
        self.assertEqual(read_checkpoint(path)["checkpoint_metadata"]["tokenizer"],
                         tokenizer)
        self.assertNotEqual(tokenizer["sha256"], file_sha256(self.data["tokenizer"]))

    # --- schema version handling --------------------------------------------

    def test_a_newer_schema_version_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"].update(
            schema_version=CHECKPOINT_SCHEMA_VERSION + 1))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn(f"schema {CHECKPOINT_SCHEMA_VERSION + 1}", message)
        self.assertIn("newer mini-llm", message)

    def test_an_older_metadata_version_is_refused(self):
        """Version 1 was the unversioned layout; a block claiming it never existed."""
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"].update(schema_version=1))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        self.assertIn("schema 1", str(ctx.exception))

    def test_metadata_without_a_usable_schema_version_is_refused(self):
        for i, version in enumerate([None, "2", True]):
            with self.subTest(schema_version=version):
                path = self.save(self.cfg(), name=f"unversioned_{i}.pt")
                self.rewrite(path, lambda c: c["checkpoint_metadata"].update(
                    schema_version=version))
                with self.assertRaises(ValueError) as ctx:
                    read_checkpoint(path)
                self.assertIn("schema_version", str(ctx.exception))

    def test_a_checkpoint_without_metadata_still_loads(self):
        """Schema 1 kept these facts in `config`; they are read, not guessed."""
        cfg = self.cfg()
        path = self.save(cfg, step=3)
        self.rewrite(path, lambda c: c.pop("checkpoint_metadata"))
        meta = read_checkpoint(path)["checkpoint_metadata"]
        self.assertEqual(meta["schema_version"], 1)
        self.assertEqual(meta["model"]["context_length"], cfg.context_length)
        self.assertEqual(meta["model"]["vocab_size"], cfg.vocab_size)
        self.assertEqual(meta["tokenizer"]["path"], cfg.tokenizer_path)
        self.assertEqual(meta["progress"]["step"], 3)
        # The weights still rebuild, from the same config as before the change.
        model, loaded_cfg = load_model(path)
        self.assertEqual(model.context_length, cfg.context_length)
        self.assertEqual(loaded_cfg.to_dict(), cfg.to_dict())

    def test_a_legacy_checkpoint_without_a_config_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: (c.pop("checkpoint_metadata"), c.pop("config")))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("config", message)
        self.assertIn("must not be guessed", message)

    def test_a_legacy_checkpoint_missing_the_model_shape_is_refused(self):
        """A default vocab_size/context_length is a different model, not a default."""
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: (c.pop("checkpoint_metadata"),
                                      c["config"].pop("context_length")))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("context_length", message)
        self.assertIn("must not be guessed", message)

    # --- incomplete or contradictory metadata is refused --------------------

    def test_metadata_without_a_tokenizer_is_refused(self):
        for i, tokenizer in enumerate([{}, {"sha256": "0" * 64}]):
            with self.subTest(tokenizer=tokenizer):
                path = self.save(self.cfg(), name=f"no_tokenizer_{i}.pt")
                self.rewrite(path, lambda c: c["checkpoint_metadata"].update(
                    tokenizer=tokenizer))
                with self.assertRaises(ValueError) as ctx:
                    read_checkpoint(path)
                self.assertIn("tokenizer.path", str(ctx.exception))

    def test_metadata_without_the_model_shape_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"]["model"].pop("d_model"))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        self.assertIn("model.d_model", str(ctx.exception))

    def test_metadata_without_progress_is_refused(self):
        for i, progress in enumerate([{}, {"step": None}]):
            with self.subTest(progress=progress):
                path = self.save(self.cfg(), name=f"no_progress_{i}.pt")
                self.rewrite(path, lambda c: c["checkpoint_metadata"].update(
                    progress=progress))
                with self.assertRaises(ValueError) as ctx:
                    read_checkpoint(path)
                self.assertIn("progress.step", str(ctx.exception))

    def test_metadata_contradicting_the_config_is_refused(self):
        """config is what the loaders rebuild from, so it may not drift from the block."""
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["config"].update(context_length=512))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("context_length", message)

    def test_metadata_step_contradicting_checkpoint_step_is_refused(self):
        path = self.save(self.cfg(), step=4)
        self.rewrite(path, lambda c: c["checkpoint_metadata"]["progress"].update(step=5))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("step", message)

    def test_metadata_tokenizer_path_contradicting_config_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"]["tokenizer"].update(
            path="data/other_tokenizer.json"))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("tokenizer_path", message)

    def test_metadata_max_steps_contradicting_config_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"]["progress"].update(
            max_steps=self.cfg().max_steps + 10))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("max_steps", message)

    def test_metadata_tokenizer_sha256_contradicting_provenance_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["checkpoint_metadata"]["tokenizer"].update(
            sha256="0" * 64))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("tokenizer_sha256", message)

    def test_model_state_shape_contradicting_metadata_is_refused(self):
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["model_state"].update(
            {"wte.weight": torch.zeros((c["checkpoint_metadata"]["model"]["vocab_size"] + 8,
                                        c["checkpoint_metadata"]["model"]["d_model"]))}))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("vocab_size", message)

    def test_position_embedding_shape_contradicting_context_length_is_refused(self):
        """wpe.weight is the second tensor the shape check exists for.

        The embedding table pins vocab_size; the position table pins
        context_length. A position table whose row count disagrees with the
        recorded context_length describes a different model, so it is refused
        on the same terms as the embedding one.
        """
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["model_state"].update(
            {"wpe.weight": torch.zeros((c["checkpoint_metadata"]["model"]["context_length"] - 4,
                                        c["checkpoint_metadata"]["model"]["d_model"]))}))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("context_length", message)

    def test_embedding_shape_contradicting_d_model_is_refused(self):
        """Both tensors are checked against d_model, not only against vocab_size."""
        path = self.save(self.cfg())
        self.rewrite(path, lambda c: c["model_state"].update(
            {"wte.weight": torch.zeros((c["checkpoint_metadata"]["model"]["vocab_size"],
                                        c["checkpoint_metadata"]["model"]["d_model"] + 4))}))
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        message = str(ctx.exception)
        self.assertIn("internally inconsistent", message)
        self.assertIn("d_model", message)

    def test_raw_unvalidated_dict_cannot_bypass_validation(self):
        """Passing a dict is not a way around validate_checkpoint().

        load_model()/load_checkpoint() accept an already-read dict so the
        in-tree callers avoid a second read. That must not become a way to load
        an unchecked checkpoint: a dict straight from torch.load, with metadata
        that contradicts its own config, is still refused.
        """
        path = self.save(self.cfg())
        raw = torch.load(path, map_location="cpu", weights_only=True)
        # A raw torch.load result carries no validation marker.
        self.assertNotIn(VALIDATED_KEY, raw)
        raw["checkpoint_metadata"]["model"]["d_ff"] = self.cfg().d_ff + 64
        # validate_checkpoint rejects it...
        with self.assertRaises(ValueError) as ctx:
            validate_checkpoint(raw, "<in-memory checkpoint>")
        self.assertIn("internally inconsistent", str(ctx.exception))
        # ...so the loaders must refuse it too rather than trusting the dict.
        with self.assertRaises(ValueError):
            load_model(raw)
        model = from_config(self.cfg())
        opt = torch.optim.AdamW(build_param_groups(model, self.cfg().weight_decay), lr=1e-3)
        with self.assertRaises(ValueError):
            load_checkpoint(raw, model, opt)

    def test_a_validated_dict_still_loads_through_both_loaders(self):
        """The marker skips re-validation, not validation of the checkpoint."""
        path = self.save(self.cfg())
        ckpt = read_checkpoint(path)
        self.assertIs(ckpt.get(VALIDATED_KEY), True)
        loaded, loaded_cfg = load_model(ckpt)
        self.assertEqual(loaded_cfg.to_dict(), self.cfg().to_dict())
        fresh = from_config(self.cfg())
        opt = torch.optim.AdamW(build_param_groups(fresh, self.cfg().weight_decay), lr=1e-3)
        self.assertEqual(load_checkpoint(ckpt, fresh, opt)["step"], 1)

    def test_a_checkpoint_missing_a_field_the_loader_needs_is_refused(self):
        for key in ("model_state", "step"):
            with self.subTest(missing=key):
                path = self.save(self.cfg(), name=f"no_{key}.pt")
                self.rewrite(path, lambda c, k=key: c.pop(k))
                with self.assertRaises(ValueError) as ctx:
                    read_checkpoint(path)
                self.assertIn(key, str(ctx.exception))

    def test_a_file_that_is_not_a_checkpoint_dict_is_refused(self):
        path = os.path.join(self.tmp.name, "not_a_ckpt.pt")
        torch.save(["not", "a", "checkpoint"], path)
        with self.assertRaises(ValueError) as ctx:
            read_checkpoint(path)
        self.assertIn("not a mini-llm checkpoint", str(ctx.exception))

    # --- progress is the whole run's, not just this segment's ----------------

    def test_the_training_loop_records_step_epoch_and_tokens(self):
        cfg = self.cfg(max_steps=3, warmup_steps=1, eval_interval=3)
        train(cfg)
        progress = read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["checkpoint_metadata"]["progress"]
        n_train_tokens = len(load_token_ids(self.data["train_bin"]))
        self.assertEqual(progress["step"], 3)
        self.assertEqual(progress["tokens_seen"], 3 * cfg.batch_size * cfg.context_length)
        self.assertAlmostEqual(progress["epoch"],
                               3 * cfg.batch_size * cfg.context_length / n_train_tokens)

    def test_a_resumed_run_keeps_counting_the_same_epoch(self):
        cfg = self.cfg(max_steps=3, warmup_steps=1, eval_interval=3)
        train(cfg)
        first = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        train(self.cfg(max_steps=6, warmup_steps=1, eval_interval=3),
              resume_from=os.path.join(self.ckpt_dir, "step_3.pt"), start_step=4)
        progress = read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["checkpoint_metadata"]["progress"]
        self.assertEqual(progress["step"], 6)
        # The continuation adds to the count the checkpoint already carried; it
        # does not restart the run's epoch at the resume point.
        self.assertEqual(progress["tokens_seen"],
                         2 * 3 * cfg.batch_size * cfg.context_length)
        self.assertGreater(progress["epoch"],
                           first["checkpoint_metadata"]["progress"]["epoch"])

    def test_programmatic_resume_without_start_step_resumes_from_checkpoint_step(self):
        """train(cfg, resume_from=...) without start_step continues at ckpt['step'] + 1."""
        cfg = self.cfg(max_steps=3, warmup_steps=1, eval_interval=3)
        train(cfg)
        step_3_ckpt = read_checkpoint(os.path.join(self.ckpt_dir, "step_3.pt"))
        self.assertEqual(step_3_ckpt["step"], 3)
        tokens_at_step_3 = step_3_ckpt["checkpoint_metadata"]["progress"]["tokens_seen"]
        self.assertEqual(tokens_at_step_3, 3 * cfg.batch_size * cfg.context_length)

        # Call train() with resume_from without passing start_step
        train(self.cfg(max_steps=6, warmup_steps=1, eval_interval=3),
              resume_from=os.path.join(self.ckpt_dir, "step_3.pt"))
        final_ckpt = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        progress = final_ckpt["checkpoint_metadata"]["progress"]
        self.assertEqual(final_ckpt["step"], 6)
        self.assertEqual(progress["step"], 6)
        # Earlier steps 1..3 must NOT have been rerun; tokens_seen must account for exactly 6 steps
        expected_tokens = 6 * cfg.batch_size * cfg.context_length
        self.assertEqual(progress["tokens_seen"], expected_tokens)
        n_train_tokens = len(load_token_ids(self.data["train_bin"]))
        self.assertAlmostEqual(progress["epoch"], expected_tokens / n_train_tokens)

    def test_validate_checkpoint_reports_without_loading_from_disk(self):
        """The check is a pure function of the dict, so it is usable before a load."""
        cfg = self.cfg()
        ckpt = read_checkpoint(self.save(cfg))
        self.assertEqual(validate_checkpoint(ckpt, "somewhere.pt"),
                         ckpt["checkpoint_metadata"])
        with self.assertRaises(ValueError):
            validate_checkpoint({"model_state": {}, "step": 1}, "somewhere.pt")


class TestGenerationSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(vocab_size=40, context_length=8, n_layers=1, n_heads=2,
                          d_model=16, d_ff=32, dropout=0.0)
        self.ckpt = os.path.join(self.tmp.name, "ckpt.pt")
        torch.manual_seed(0)
        model = from_config(self.cfg)
        opt = torch.optim.AdamW(build_param_groups(model, self.cfg.weight_decay), lr=1e-3)
        save_checkpoint(self.ckpt, model, opt, step=1, cfg=self.cfg, lr=1e-3)

    def run_main(self, *extra) -> str:
        argv = sys.argv
        sys.argv = ["generate.py", "--checkpoint", self.ckpt, "--tokens", "12",
                    "--temperature", "1.0", "--top-k", "8", *extra]
        try:
            train_out = io.StringIO()
            with contextlib.redirect_stdout(train_out):
                generate_main()
            return train_out.getvalue()
        finally:
            sys.argv = argv

    def test_same_seed_is_reproducible(self):
        a = self.run_main("--seed", "1234")
        b = self.run_main("--seed", "1234")
        self.assertEqual(a, b)
        self.assertGreater(len(a.strip()), 0)

    def test_different_seed_differs(self):
        self.assertNotEqual(self.run_main("--seed", "1"), self.run_main("--seed", "2"))

    def test_without_seed_sampling_stays_stochastic(self):
        runs = {self.run_main() for _ in range(3)}
        self.assertGreater(len(runs), 1)


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


class TestShippedData(unittest.TestCase):
    """The committed corpus and the committed artifacts must stay in agreement.

    Byte-level BPE is line-ending sensitive (LF -> vocab 308, CRLF -> vocab 310),
    so a checkout that rewrites data/raw/train.txt would silently disagree with
    data/processed/meta.json. .gitattributes pins the bytes; this catches a
    regression in either.
    """

    def test_shipped_corpus_reproduces_the_committed_vocab(self):
        corpus = os.path.join(ROOT, "data", "raw", "train.txt")
        meta = load_data_meta(os.path.join(ROOT, "data", "processed", "train.bin"))
        with open(corpus, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw, "data/raw/train.txt must stay LF (-text in .gitattributes)")
        with tempfile.TemporaryDirectory() as tmp:
            tok = train_bpe_tokenizer(
                [corpus], os.path.join(tmp, "t.json"), vocab_size=8192, min_frequency=2
            )
            self.assertEqual(tok.get_vocab_size(), meta["vocab_size"])

    def test_shipped_tokens_are_inside_the_committed_vocab(self):
        meta = load_data_meta(os.path.join(ROOT, "data", "processed", "train.bin"))
        for name in ("train", "val"):
            ids = load_token_ids(os.path.join(ROOT, "data", "processed", f"{name}.bin"))
            self.assertLess(int(ids.max()), meta["vocab_size"])
            self.assertEqual(len(ids), meta[f"{name}_tokens"])

    def test_shipped_tokenizer_matches_the_committed_vocab(self):
        # generate.py loads this exact path, and the sibling tests above only ever
        # retrain into a temp dir, so nothing else covers the committed artifact:
        # deleting or swapping it broke generation while the rest of the suite passed.
        path = os.path.join(ROOT, "data", "tokenizer.json")
        meta = load_data_meta(os.path.join(ROOT, "data", "processed", "train.bin"))
        self.assertTrue(os.path.exists(path),
                        "data/tokenizer.json is a tracked artifact; restore it from git")
        tok = load_tokenizer(path)
        self.assertEqual(tok.get_vocab_size(), meta["vocab_size"])
        for special in ("<pad>", "<unk>", "<bos>", "<eos>"):
            self.assertIsNotNone(tok.token_to_id(special), f"missing {special}")


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


class TestDataProvenance(unittest.TestCase):
    """Non-default data/tokenizer paths, and resuming only onto the same data.

    The bug these guard: Config.tokenizer_path had no CLI flag and prepare_data.py
    recorded no tokenizer in meta.json, so every checkpoint stored the default
    paths and a resume could continue against an unrelated corpus/tokenizer.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Two independent data sets in non-default directories. The second has its
        # own corpus and vocabulary, so every artifact differs by content; setUp
        # asserts that rather than trusting the inputs to differ.
        self.a = build_data_set(os.path.join(self.tmp.name, "set_a"))
        self.b = build_data_set(os.path.join(self.tmp.name, "set_b"), vocab_size=256,
                                corpus_text=OTHER_CORPUS)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")
        self.assertNotEqual(file_sha256(self.a["train_bin"]),
                            file_sha256(self.b["train_bin"]))
        self.assertNotEqual(file_sha256(self.a["tokenizer"]),
                            file_sha256(self.b["tokenizer"]))

    def cfg_for(self, data: dict, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(data["train_bin"])["vocab_size"],
                  context_length=16, n_layers=1, n_heads=2, d_model=16, d_ff=32,
                  batch_size=2, max_steps=9, warmup_steps=2, eval_interval=3,
                  eval_batches=2, train_bin=data["train_bin"], val_bin=data["val_bin"],
                  tokenizer_path=data["tokenizer"],
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def run_main(self, *argv) -> None:
        saved = sys.argv
        sys.argv = ["train.py", *argv]
        try:
            train_main()
        finally:
            sys.argv = saved

    def train_partial(self) -> str:
        """Train data set A to completion and return the mid-run step_3.pt path.

        The full run is deliberate: the checkpoint's own max_steps is what a later
        resume honours, so a checkpoint saved at its final step would (correctly)
        refuse to continue. Running to 9 leaves step_3.pt resumable at step 4.
        """
        train(self.cfg_for(self.a))
        return os.path.join(self.ckpt_dir, "step_3.pt")

    # --- preparation metadata --------------------------------------------

    def test_meta_records_tokenizer_and_data_provenance(self):
        meta = load_data_meta(self.a["train_bin"])
        self.assertEqual(meta["tokenizer_path"], self.a["tokenizer"])
        self.assertEqual(meta["train_bin"], self.a["train_bin"])
        self.assertEqual(meta["val_bin"], self.a["val_bin"])
        for key, path in (("tokenizer_sha256", self.a["tokenizer"]),
                          ("train_sha256", self.a["train_bin"]),
                          ("val_sha256", self.a["val_bin"])):
            self.assertEqual(meta[key], file_sha256(path), key)

    def test_meta_without_provenance_is_refused(self):
        """A pre-provenance meta.json is not trusted; it is not silently accepted."""
        meta = load_data_meta(self.a["train_bin"])
        for key in ("tokenizer_path", "tokenizer_sha256"):
            del meta[key]
        with open(os.path.join(self.a["root"], "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        with self.assertRaises(ValueError) as ctx:
            self.cfg_for(self.a).data_provenance()
        self.assertIn("provenance", str(ctx.exception))

    def test_changed_tokenizer_is_refused(self):
        with open(self.a["tokenizer"], "a", encoding="utf-8") as f:
            f.write(" ")
        with self.assertRaises(ValueError) as ctx:
            self.cfg_for(self.a).data_provenance()
        self.assertIn("changed since", str(ctx.exception))

    def test_missing_tokenizer_artifact_is_named(self):
        os.remove(self.a["tokenizer"])
        with self.assertRaises(FileNotFoundError) as ctx:
            self.cfg_for(self.a).data_provenance()
        self.assertIn("tokenizer.json", str(ctx.exception))


# --- non-default paths through the CLI -------------------------------

    def test_cli_records_non_default_tokenizer_and_data_paths(self):
        """--tokenizer / --train-bin / --val-bin land in the checkpoint verbatim."""
        # Architecture flags are not on the CLI, so this builds the default model
        # at the data set's vocab for 2 steps; what matters is what gets recorded.
        self.run_main("--max-steps", "2", "--context-length", "16",
                      "--batch-size", "2",
                      "--checkpoint-dir", self.ckpt_dir,
                      "--train-bin", self.a["train_bin"],
                      "--val-bin", self.a["val_bin"],
                      "--tokenizer", self.a["tokenizer"])
        ckpt = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        stored = ckpt["config"]
        self.assertEqual(stored["tokenizer_path"], self.a["tokenizer"])
        self.assertEqual(stored["train_bin"], self.a["train_bin"])
        self.assertEqual(stored["val_bin"], self.a["val_bin"])
        # And the checkpoint identifies that same tokenizer by content.
        self.assertEqual(ckpt["data_provenance"]["tokenizer_sha256"],
                         file_sha256(self.a["tokenizer"]))

    def test_config_for_data_adopts_the_prepared_tokenizer(self):
        """A non-default data set pulls its own tokenizer/val paths into the config."""
        cfg = config_for_data(self.b["train_bin"], n_layers=1, n_heads=2,
                              d_model=16, d_ff=32)
        self.assertEqual(cfg.tokenizer_path, self.b["tokenizer"])
        self.assertEqual(cfg.val_bin, self.b["val_bin"])
        self.assertEqual(cfg.vocab_size,
                         load_data_meta(self.b["train_bin"])["vocab_size"])

    def test_checkpoint_records_data_provenance(self):
        self.train_partial()
        prov = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))["data_provenance"]
        self.assertEqual(prov["tokenizer_sha256"], file_sha256(self.a["tokenizer"]))
        self.assertEqual(prov["train_sha256"], file_sha256(self.a["train_bin"]))
        self.assertEqual(prov["val_sha256"], file_sha256(self.a["val_bin"]))
        self.assertEqual(prov["vocab_size"],
                         load_data_meta(self.a["train_bin"])["vocab_size"])

    def test_checkpoint_still_loads_with_weights_only(self):
        self.train_partial()
        path = os.path.join(self.ckpt_dir, "final.pt")
        self.assertIn("data_provenance",
                      torch.load(path, map_location="cpu", weights_only=True))

    # --- matching resume --------------------------------------------------

    def test_matching_resume_succeeds_on_non_default_paths(self):
        mid = self.train_partial()
        # max_steps 12 extends the run; without it the checkpoint's own 9 is the
        # ceiling and step 4 would be refused for being past the end.
        train(self.cfg_for(self.a, max_steps=12), resume_from=mid, start_step=4)
        done = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        self.assertEqual(done["step"], 12)
        self.assertEqual(done["data_provenance"]["tokenizer_sha256"],
                         file_sha256(self.a["tokenizer"]))

    def test_matching_resume_from_checkpoint_paths_needs_no_flags(self):
        """A bare --resume works: the checkpoint carries its own verified paths."""
        mid = self.train_partial()
        self.run_main("--resume", mid, "--max-steps", "12")
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 12)

    def test_relocated_but_identical_artifacts_still_resume(self):
        """Digests, not paths, are the contract: a moved copy is the same data."""
        mid = self.train_partial()
        moved = os.path.join(self.tmp.name, "moved")
        shutil.copytree(self.a["root"], moved)
        moved_set = {"train_bin": os.path.join(moved, "train.bin"),
                     "val_bin": os.path.join(moved, "val.bin"),
                     "tokenizer": os.path.join(moved, "tokenizer.json")}
        train(self.cfg_for(moved_set, max_steps=12), resume_from=mid, start_step=4)
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 12)


# --- mismatch is refused, never a silent fallback --------------------

    def test_resume_against_another_tokenizer_is_refused(self):
        mid = self.train_partial()
        with self.assertRaises(SystemExit) as ctx:
            train(self.cfg_for(self.a, tokenizer_path=self.b["tokenizer"]),
                  resume_from=mid, start_step=4)
        self.assertIn("tokenizer_sha256", str(ctx.exception))

    def test_resume_against_another_dataset_is_refused(self):
        mid = self.train_partial()
        with self.assertRaises(SystemExit) as ctx:
            train(self.cfg_for(self.b), resume_from=mid, start_step=4)
        message = str(ctx.exception)
        self.assertIn("train_sha256", message)
        self.assertIn("tokenizer_sha256", message)

    def test_resume_against_another_val_split_is_refused(self):
        mid = self.train_partial()
        mixed = dict(self.a, val_bin=self.b["val_bin"])
        with self.assertRaises(SystemExit) as ctx:
            train(self.cfg_for(mixed), resume_from=mid, start_step=4)
        self.assertIn("val_sha256", str(ctx.exception))

    def test_cli_resume_with_a_foreign_data_set_is_refused(self):
        """The bug's exact failure mode: a resume whose flags point elsewhere."""
        mid = self.train_partial()
        with self.assertRaises(SystemExit) as ctx:
            self.run_main("--resume", mid,
                          "--train-bin", self.b["train_bin"],
                          "--val-bin", self.b["val_bin"],
                          "--tokenizer", self.b["tokenizer"],
                          "--checkpoint-dir", self.ckpt_dir)
        self.assertIn("refusing to resume", str(ctx.exception))

    def test_resume_of_a_checkpoint_without_provenance_is_refused(self):
        """An unverifiable checkpoint is refused, not resumed on trust."""
        mid = self.train_partial()
        ckpt = torch.load(mid, map_location="cpu", weights_only=True)
        del ckpt["data_provenance"]
        torch.save(ckpt, mid)
        with self.assertRaises(SystemExit) as ctx:
            train(self.cfg_for(self.a, max_steps=12), resume_from=mid, start_step=4)
        self.assertIn("no data provenance", str(ctx.exception))

    def test_a_foreign_tokenizer_cannot_generate_from_a_checkpoint(self):
        self.train_partial()
        path = os.path.join(self.ckpt_dir, "final.pt")
        with self.assertRaises(SystemExit) as ctx:
            check_tokenizer_provenance(read_checkpoint(path), self.b["tokenizer"])
        self.assertIn("not the tokenizer", str(ctx.exception))
        # The matching tokenizer passes.
        check_tokenizer_provenance(read_checkpoint(path), self.a["tokenizer"])


if __name__ == "__main__":
    unittest.main()
