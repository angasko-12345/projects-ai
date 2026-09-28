"""Throughput-optimization tests: batch-level conversion, threads, compile.

Covers the CPU-throughput changes without touching model behavior:
loader batches must be value-identical to the legacy per-sample path.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch
from test_pipeline import build_data  # noqa: E402  (shared corpus builder)
from src.config import Config, load_data_meta  # noqa: E402
from src.dataset import TokenDataset, build_dataloader, collate_windows  # noqa: E402
from src.train import (configure_torch_threads, make_parser, maybe_compile_model,  # noqa: E402
                       read_checkpoint, train)


def legacy_batches(ds: TokenDataset, batch_size: int):
    """Reference batches via the per-sample __getitem__ path (the old hot path)."""
    out = []
    for s in range(0, len(ds), batch_size):
        idxs = list(range(s, min(s + batch_size, len(ds))))
        out.append((torch.stack([ds[i][0] for i in idxs]),
                    torch.stack([ds[i][1] for i in idxs])))
    return out


class TestBatchEquivalence(unittest.TestCase):
    def test_fetch_windows_matches_stacked_getitem(self):
        toks = np.arange(200, dtype=np.uint16)
        ds = TokenDataset(toks, context_length=16, stride=3)
        idxs = [0, 5, 11]
        raw = ds.fetch_windows(idxs)
        self.assertEqual(raw.dtype, np.uint16)  # no conversion on the fetch path
        x, y = collate_windows(raw)
        ex = torch.stack([ds[i][0] for i in idxs])
        ey = torch.stack([ds[i][1] for i in idxs])
        self.assertTrue(torch.equal(x, ex))
        self.assertTrue(torch.equal(y, ey))
    def test_loader_batches_match_legacy_path(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        toks = np.arange(500, dtype=np.uint16)
        path = os.path.join(tmp, "t.bin")
        toks.tofile(path)
        for stride, batch_size in ((1, 8), (3, 8), (16, 4)):
            ds = TokenDataset(toks, context_length=32, stride=stride)
            loader = build_dataloader(path, 32, batch_size, shuffle=False,
                                      drop_last=False, stride=stride)
            for (x, y), (ex, ey) in zip(loader, legacy_batches(ds, batch_size)):
                self.assertTrue(torch.equal(x, ex), f"stride={stride}")
                self.assertTrue(torch.equal(y, ey), f"stride={stride}")
                self.assertEqual(x.dtype, torch.int64)

    def test_shuffled_loader_is_a_permutation_with_same_values(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        toks = (np.arange(300, dtype=np.uint16) * 7 % 251)
        path = os.path.join(tmp, "t.bin")
        toks.tofile(path)
        torch.manual_seed(0)
        got = [(x, y) for x, y in build_dataloader(
            path, 16, 4, shuffle=True, drop_last=True)]
        ds = TokenDataset(toks, context_length=16)
        expected = []
        for a, b in legacy_batches(ds, 4):
            expected.extend((tuple(a[r].tolist()), tuple(b[r].tolist()))
                            for r in range(a.shape[0]))
        got_rows = []
        for x, y in got:
            got_rows.extend((tuple(x[r].tolist()), tuple(y[r].tolist()))
                            for r in range(x.shape[0]))
        self.assertCountEqual(got_rows, expected)

    def test_ragged_tail_counts_actual_tokens(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        toks = np.arange(100, dtype=np.uint16)
        path = os.path.join(tmp, "t.bin")
        toks.tofile(path)
        loader = build_dataloader(path, 16, 8, shuffle=False, drop_last=False)
        sizes = [x.shape[0] for x, _ in loader]
        self.assertEqual(sizes, [8] * 10 + [4])  # 84 samples, tail of 4
        self.assertEqual(sum(s * 16 for s in sizes), 84 * 16)


class TestDatasetValidation(unittest.TestCase):
    def test_rejects_bad_stride(self):
        with self.assertRaises(ValueError):
            TokenDataset(np.arange(100), context_length=8, stride=0)

    def test_rejects_too_small_corpus(self):
        with self.assertRaises(ValueError) as ctx:
            TokenDataset(np.arange(5), context_length=16)
        self.assertIn("context_length", str(ctx.exception))

    def test_loader_diagnoses_missing_full_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.bin")
            np.arange(100, dtype=np.uint16).tofile(path)
            with self.assertRaises(ValueError) as ctx:
                build_dataloader(path, context_length=16, batch_size=10_000)
            self.assertIn("batch_size", str(ctx.exception))


class TestThreadOptions(unittest.TestCase):
    def test_defaults_leave_torch_alone(self):
        args = make_parser().parse_args([])
        self.assertIsNone(args.torch_threads)
        self.assertIsNone(args.torch_interop_threads)
        self.assertIsNone(args.compile)

    def test_flags_are_parsed(self):
        args = make_parser().parse_args(
            ["--torch-threads", "4", "--torch-interop-threads", "2", "--compile"])
        self.assertEqual(args.torch_threads, 4)
        self.assertEqual(args.torch_interop_threads, 2)
        self.assertTrue(args.compile)

    def test_configure_sets_and_reports_threads(self):
        orig = torch.get_num_threads()
        self.addCleanup(torch.set_num_threads, orig)
        cfg = Config(torch_threads=2)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with mock.patch("src.train.torch.set_num_interop_threads") as inter:
                configure_torch_threads(Config(torch_threads=None,
                                               torch_interop_threads=3))
                inter.assert_called_once_with(3)
            configure_torch_threads(cfg)
        self.assertEqual(torch.get_num_threads(), 2)
        self.assertIn("torch threads: intraop=", buf.getvalue())

    def test_configure_is_noop_when_unset(self):
        with mock.patch("src.train.torch.set_num_threads") as intra, \
             mock.patch("src.train.torch.set_num_interop_threads") as inter:
            configure_torch_threads(Config())
        intra.assert_not_called()
        inter.assert_not_called()

    def test_late_interop_call_warns_instead_of_crashing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with mock.patch("src.train.torch.set_num_interop_threads",
                             side_effect=RuntimeError("too late")):
                configure_torch_threads(Config(torch_interop_threads=2))
        self.assertIn("warning", buf.getvalue())

    def test_rejects_non_positive_thread_counts(self):
        with self.assertRaises(AssertionError):
            Config(torch_threads=0)
        with self.assertRaises(AssertionError):
            Config(torch_interop_threads=-1)

    def test_rejects_non_positive_vocab_and_context(self):
        for kw in ({"vocab_size": 0}, {"vocab_size": -5},
                   {"context_length": 0}, {"context_length": -1}):
            with self.assertRaises(AssertionError, msg=kw):
                Config(**kw)


class TestCompileFlag(unittest.TestCase):
    def test_disabled_by_default_and_is_passthrough(self):
        self.assertFalse(Config().compile)
        model = torch.nn.Linear(4, 4)
        self.assertIs(maybe_compile_model(model, False), model)

    def test_missing_compile_fails_cleanly(self):
        with mock.patch.object(torch, "compile", None, create=True):
            with self.assertRaises(SystemExit) as ctx:
                maybe_compile_model(torch.nn.Linear(4, 4), True)
        self.assertIn("--compile", str(ctx.exception))

    def test_broken_compile_fails_cleanly(self):
        with mock.patch.object(torch, "compile",
                               side_effect=RuntimeError("no backend"),
                               create=True):
            with self.assertRaises(SystemExit) as ctx:
                maybe_compile_model(torch.nn.Linear(4, 4), True)
        self.assertIn("--compile", str(ctx.exception))

    def test_backend_failure_at_first_forward_fails_cleanly(self):
        from src.model import from_config
        model = from_config(Config(vocab_size=32, context_length=8, n_layers=1,
                                   n_heads=2, d_model=16, d_ff=32))

        def explode(*a, **k):
            raise RuntimeError("no C++ compiler")

        with mock.patch.object(torch, "compile", return_value=model, create=True):
            with mock.patch.object(type(model), "forward", explode):
                with self.assertRaises(SystemExit) as ctx:
                    maybe_compile_model(model, True)
        self.assertIn("--compile", str(ctx.exception))

class TestTrainThroughput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")
        orig = torch.get_num_threads()
        self.addCleanup(torch.set_num_threads, orig)

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=6, warmup_steps=2, eval_interval=3,
                  eval_batches=2, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  checkpoint_dir=self.ckpt_dir, seed=0)
        kw.update(over)
        return Config(**kw)

    def test_train_reports_throughput_and_writes_checkpoints(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            train(self.tiny(torch_threads=2))
        out = buf.getvalue()
        self.assertIn("tokens/sec", out)
        self.assertIn("steps/sec", out)
        self.assertIn("torch threads: intraop=2", out)
        final = os.path.join(self.ckpt_dir, "final.pt")
        self.assertTrue(os.path.exists(final))
        ckpt = read_checkpoint(final)
        self.assertEqual(ckpt["step"], 6)
        # Uncompiled checkpoints carry bare parameter names.
        self.assertFalse(any(k.startswith("_orig_mod.") for k in ckpt["model_state"]))

    def test_old_checkpoint_without_perf_fields_still_loads(self):
        cfg = self.tiny()
        d = cfg.to_dict()
        for key in ("torch_threads", "torch_interop_threads", "compile"):
            del d[key]
        restored = Config.from_dict(d)
        self.assertIsNone(restored.torch_threads)
        self.assertFalse(restored.compile)

    def test_resume_with_thread_settings(self):
        train(self.tiny(max_steps=3))
        train(self.tiny(torch_threads=2, max_steps=9), resume_from=os.path.join(
            self.ckpt_dir, "step_3.pt"), start_step=4)
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 9)


if __name__ == "__main__":
    unittest.main()
