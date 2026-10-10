"""Checkpoint safety: atomic saves and rotation of numbered step_*.pt files.

CPU only. The atomic-save tests drive save_checkpoint directly; the rotation
tests create the files a training loop would leave behind; the end-to-end test
runs the real loop through train() with eval on every step.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prepare_data
from src.config import Config, load_data_meta
from src.model import from_config
from src.train import (apply_cli_config, build_param_groups, list_step_checkpoints,
                       make_parser, read_checkpoint, rotate_checkpoints, save_checkpoint,
                       train)

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
        "--vocab-size", "512",
        "--min-frequency", "1",
        "--val-frac", "0.2",
        "--context-length", str(context_length),
    ]
    try:
        prepare_data.main()
    finally:
        sys.argv = argv
    return os.path.join(tmp, "train.bin")


def touch(directory: str, *names: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for name in names:
        with open(os.path.join(directory, name), "wb") as f:
            f.write(b"x" * 8)


def temp_files(directory: str) -> list[str]:
    return sorted(n for n in os.listdir(directory) if n.endswith(".tmp"))


class TestAtomicSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(vocab_size=64, context_length=8, n_layers=2, n_heads=2,
                          d_model=32, d_ff=64, max_steps=2, warmup_steps=1,
                          checkpoint_dir=self.tmp.name)
        self.model = from_config(self.cfg)
        self.optimizer = torch.optim.AdamW(
            build_param_groups(self.model, self.cfg.weight_decay), lr=1e-3)

    def save(self, path: str, step: int) -> None:
        save_checkpoint(path, self.model, self.optimizer, step, self.cfg, lr=1e-3)

    def test_writes_a_readable_checkpoint_and_leaves_no_temp(self):
        path = os.path.join(self.tmp.name, "step_1.pt")
        self.save(path, 1)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(read_checkpoint(path)["step"], 1)
        self.assertEqual(temp_files(self.tmp.name), [])

    def test_replaces_an_existing_checkpoint(self):
        path = os.path.join(self.tmp.name, "step_1.pt")
        self.save(path, 1)
        self.save(path, 2)
        self.assertEqual(read_checkpoint(path)["step"], 2)
        self.assertEqual(temp_files(self.tmp.name), [])

    def test_failed_save_preserves_the_previous_checkpoint_and_cleans_temp(self):
        path = os.path.join(self.tmp.name, "step_1.pt")
        self.save(path, 1)
        good_step = read_checkpoint(path)["step"]

        def fail_after_partial_write(obj, handle):
            # A short write to the temp file, then a failure: exactly the shape of
            # a crash mid-save. Nothing here should reach the destination.
            handle.write(b"partial checkpoint")
            raise RuntimeError("simulated disk failure")

        with mock.patch("src.train.torch.save", side_effect=fail_after_partial_write):
            with self.assertRaises(RuntimeError):
                self.save(path, 2)

        # The good checkpoint is intact and no temp file was left behind.
        self.assertEqual(read_checkpoint(path)["step"], good_step)
        self.assertEqual(temp_files(self.tmp.name), [])


class TestRotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def names(self) -> list[str]:
        return sorted(os.listdir(self.dir))

    def test_keeps_only_the_newest_numbered_checkpoints(self):
        touch(self.dir, "step_3.pt", "step_6.pt", "step_9.pt", "step_12.pt", "step_15.pt")
        removed = rotate_checkpoints(self.dir, keep_last=2)
        self.assertEqual([os.path.basename(p) for p in removed],
                         ["step_3.pt", "step_6.pt", "step_9.pt"])
        self.assertEqual(self.names(), ["step_12.pt", "step_15.pt"])

    def test_preserves_final_and_unrelated_files(self):
        touch(self.dir, "step_1.pt", "step_2.pt", "step_3.pt",
              "final.pt", "best.pt", "notes.txt")
        rotate_checkpoints(self.dir, keep_last=1)
        self.assertEqual(self.names(), ["best.pt", "final.pt", "notes.txt", "step_3.pt"])

    def test_removes_nothing_when_under_the_limit(self):
        touch(self.dir, "step_1.pt", "step_2.pt")
        self.assertEqual(rotate_checkpoints(self.dir, keep_last=5), [])
        self.assertEqual(self.names(), ["step_1.pt", "step_2.pt"])

    def test_never_deletes_the_protected_checkpoint(self):
        # keep_last=1 would normally drop step_1, but step_1 is the file a save
        # just wrote, so it is protected even though its step number is smallest.
        protected = os.path.join(self.dir, "step_1.pt")
        touch(self.dir, "step_1.pt", "step_9.pt")
        removed = rotate_checkpoints(self.dir, keep_last=1, keep=protected)
        self.assertEqual(removed, [])
        self.assertTrue(os.path.exists(protected))

    def test_ignores_files_that_are_not_numbered_checkpoints(self):
        touch(self.dir, "step_2.pt", "step_10.pt")
        with open(os.path.join(self.dir, "step_x.pt"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.dir, "step_3.pt.123.tmp"), "wb") as f:
            f.write(b"x")
        # Only step_2.pt and step_10.pt are numbered; the odd names are untouched.
        rotate_checkpoints(self.dir, keep_last=1)
        self.assertEqual(self.names(), ["step_10.pt", "step_3.pt.123.tmp", "step_x.pt"])

    def test_list_step_checkpoints_orders_by_step(self):
        touch(self.dir, "step_10.pt", "step_2.pt", "step_1.pt", "final.pt")
        self.assertEqual([step for step, _ in list_step_checkpoints(self.dir)], [1, 2, 10])

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(list_step_checkpoints(os.path.join(self.dir, "nope")), [])
        self.assertEqual(rotate_checkpoints(os.path.join(self.dir, "nope"), keep_last=2), [])

    def test_invalid_keep_last_is_rejected(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                rotate_checkpoints(self.dir, keep_last=bad)

    def test_config_rejects_non_positive_keep_last(self):
        for bad in (0, -1):
            with self.assertRaises(AssertionError):
                Config(keep_last=bad)


class TestKeepLastCli(unittest.TestCase):
    def test_default_is_three(self):
        self.assertEqual(Config().keep_last, 3)

    def test_flag_reaches_config(self):
        cfg = Config(context_length=16, max_steps=2, warmup_steps=1)
        apply_cli_config(cfg, make_parser().parse_args(["--keep-last", "7"]))
        self.assertEqual(cfg.keep_last, 7)

    def test_missing_flag_leaves_the_default(self):
        cfg = Config(context_length=16, max_steps=2, warmup_steps=1)
        apply_cli_config(cfg, make_parser().parse_args([]))
        self.assertEqual(cfg.keep_last, 3)


class TestTrainingLoopRotates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(vocab_size=load_data_meta(self.train_bin)["vocab_size"],
                  context_length=16, n_layers=2, n_heads=2, d_model=32, d_ff=64,
                  batch_size=2, max_steps=4, warmup_steps=1, eval_interval=1,
                  eval_batches=1, train_bin=self.train_bin,
                  val_bin=os.path.join(self.tmp.name, "val.bin"),
                  tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
                  checkpoint_dir=self.ckpt_dir, seed=0, keep_last=2)
        kw.update(over)
        return Config(**kw)

    def test_loop_keeps_the_newest_and_the_final_checkpoint(self):
        with contextlib.redirect_stdout(io.StringIO()):
            train(self.tiny())
        # eval on every step writes step_1..step_4; keep_last=2 leaves the newest
        # two, and final.pt is written separately and never rotated.
        self.assertEqual(sorted(os.listdir(self.ckpt_dir)),
                         ["final.pt", "step_3.pt", "step_4.pt"])
        self.assertEqual(read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["step"], 4)

    def test_keep_last_one_keeps_only_the_latest(self):
        with contextlib.redirect_stdout(io.StringIO()):
            train(self.tiny(keep_last=1))
        self.assertEqual(sorted(os.listdir(self.ckpt_dir)),
                         ["final.pt", "step_4.pt"])


if __name__ == "__main__":
    unittest.main()
