"""Pipeline tests: data prep, dataset, training loop, config/data agreement. CPU only."""

import contextlib
import io
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
from src.generate import main as generate_main
from src.model import from_config
from src.tokenizer import load_tokenizer, train_bpe_tokenizer
from src.train import (build_param_groups, evaluate, load_checkpoint, load_model,
                       lr_at_step, read_checkpoint, resolve_device, restore_rng_state,
                       save_checkpoint, train)
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


if __name__ == "__main__":
    unittest.main()
