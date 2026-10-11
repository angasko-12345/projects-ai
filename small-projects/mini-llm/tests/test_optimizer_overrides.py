"""Explicit optimizer overrides must survive a resume's load_state_dict.

optimizer.load_state_dict() restores the checkpoint's parameter-group
hyperparameters, so betas/weight_decay supplied on the command line used to
lose silently to whatever the checkpoint recorded. These tests pin the actual
parameter groups after loading - not the parsed config values - plus the
no-overrides path, the decay/no-decay split, the CLI wiring, and a resumed
run's follow-up checkpoint.
"""

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
from src.train import (apply_explicit_optimizer_overrides, build_param_groups,
                       load_checkpoint, read_checkpoint, save_checkpoint, train)
from src.train import main as train_main

CORPUS = (
    "The tiny language model reads a sequence of tokens and guesses the next one.\n\n"
    "Causal self attention lets every position look only at earlier positions.\n\n"
    "A transformer block has skip connections that add the input back.\n\n"
) * 12

# The checkpoint's optimizer settings, and the values a resume must override.
SAVED = {"beta1": 0.9, "beta2": 0.95, "weight_decay": 0.1}
WANTED = {"beta1": 0.8, "beta2": 0.9, "weight_decay": 0.05}


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


def tiny_cfg(train_bin: str, tmp: str, ckpt_dir: str | None = None, **over) -> Config:
    kw = dict(vocab_size=load_data_meta(train_bin)["vocab_size"],
              context_length=16, n_layers=1, n_heads=2, d_model=16, d_ff=32,
              batch_size=2, max_steps=3, warmup_steps=1, eval_interval=3,
              eval_batches=1, train_bin=train_bin,
              val_bin=os.path.join(tmp, "val.bin"),
              tokenizer_path=os.path.join(tmp, "tokenizer.json"), seed=0,
              **SAVED)
    if ckpt_dir is not None:
        kw["checkpoint_dir"] = ckpt_dir
    kw.update(over)
    return Config(**kw)


def build_optimizer(model, cfg) -> torch.optim.AdamW:
    return torch.optim.AdamW(build_param_groups(model, cfg.weight_decay),
                             lr=cfg.learning_rate, betas=(cfg.beta1, cfg.beta2),
                             weight_decay=cfg.weight_decay)


class TestLoadThenOverride(unittest.TestCase):
    """The resume sequence minus the training loop: save, rebuild, load, re-apply."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.train_bin = build_data(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def save_one(self, **over) -> str:
        cfg = tiny_cfg(self.train_bin, self.tmp.name, **over)
        model = from_config(cfg)
        path = os.path.join(self.tmp.name, "saved.pt")
        save_checkpoint(path, model, build_optimizer(model, cfg), 3, cfg)
        return path

    def resume(self, path: str, **over):
        """Fresh model+optimizer built the way a resumed run builds them."""
        cfg = tiny_cfg(self.train_bin, self.tmp.name, **over)
        model = from_config(cfg)
        optimizer = build_optimizer(model, cfg)
        load_checkpoint(read_checkpoint(path), model, optimizer)
        return optimizer, cfg

    def test_load_alone_restores_the_checkpoint_groups(self):
        """The hazard itself: load_state_dict undoes the fresh optimizer's values."""
        optimizer, cfg = self.resume(self.save_one(**SAVED), **WANTED)
        self.assertEqual(optimizer.param_groups[0]["betas"], (0.9, 0.95))
        self.assertEqual(optimizer.param_groups[0]["weight_decay"], 0.1)
        # The config says the requested values; that alone never reached the optimizer.
        self.assertEqual((cfg.beta1, cfg.beta2, cfg.weight_decay), (0.8, 0.9, 0.05))

    def test_explicit_overrides_win_in_the_actual_groups(self):
        optimizer, cfg = self.resume(self.save_one(**SAVED), **WANTED)
        apply_explicit_optimizer_overrides(
            optimizer, cfg, {"beta1", "beta2", "weight_decay"})
        for group in optimizer.param_groups:
            self.assertEqual(group["betas"], (0.8, 0.9))
        self.assertEqual(optimizer.param_groups[0]["weight_decay"], 0.05)
        self.assertEqual(optimizer.param_groups[1]["weight_decay"], 0.0)

    def test_without_explicit_fields_the_checkpoint_settings_stay(self):
        path = self.save_one(**SAVED)
        for fields in (None, (), set()):
            with self.subTest(fields=fields):
                optimizer, cfg = self.resume(path, **WANTED)
                apply_explicit_optimizer_overrides(optimizer, cfg, fields)
                self.assertEqual(optimizer.param_groups[0]["betas"], (0.9, 0.95))
                self.assertEqual(optimizer.param_groups[0]["weight_decay"], 0.1)

    def test_partial_override_blends_with_the_checkpoint(self):
        """Only beta1 passed: the other beta must stay the checkpoint's value."""
        # cfg mirrors main()'s resume flow: checkpoint values, beta1 overwritten.
        optimizer, cfg = self.resume(self.save_one(**SAVED), beta1=0.8)
        apply_explicit_optimizer_overrides(optimizer, cfg, {"beta1"})
        self.assertEqual(optimizer.param_groups[0]["betas"], (0.8, 0.95))
        self.assertEqual(optimizer.param_groups[0]["weight_decay"], 0.1)

    def test_weight_decay_override_never_reaches_the_no_decay_group(self):
        optimizer, cfg = self.resume(self.save_one(**SAVED), **WANTED)
        apply_explicit_optimizer_overrides(optimizer, cfg, {"weight_decay"})
        self.assertTrue(all(p.dim() >= 2 for p in optimizer.param_groups[0]["params"]))
        self.assertTrue(all(p.dim() < 2 for p in optimizer.param_groups[1]["params"]))
        self.assertEqual(optimizer.param_groups[0]["weight_decay"], 0.05)
        self.assertEqual(optimizer.param_groups[1]["weight_decay"], 0.0)


class TestTrainResumeOverrideWiring(unittest.TestCase):
    """train() must apply the fields and the run must keep producing checkpoints."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def cfg(self, **over):
        return tiny_cfg(self.train_bin, self.tmp.name, self.ckpt_dir, **over)

    def test_resumed_run_applies_overrides_and_saves_a_valid_checkpoint(self):
        train(self.cfg())
        train(self.cfg(max_steps=6, **WANTED),
              resume_from=os.path.join(self.ckpt_dir, "step_3.pt"), start_step=4,
              explicit_optimizer_fields={"beta1", "beta2", "weight_decay"})
        done = read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))
        self.assertEqual(done["step"], 6)
        groups = done["optimizer_state"]["param_groups"]
        self.assertEqual(groups[0]["betas"], (0.8, 0.9))
        self.assertEqual(groups[0]["weight_decay"], 0.05)
        self.assertEqual(groups[1]["weight_decay"], 0.0)

    def test_resumed_run_without_fields_keeps_the_checkpoint_groups(self):
        train(self.cfg())
        train(self.cfg(max_steps=6, **WANTED),
              resume_from=os.path.join(self.ckpt_dir, "step_3.pt"), start_step=4)
        groups = read_checkpoint(
            os.path.join(self.ckpt_dir, "final.pt"))["optimizer_state"]["param_groups"]
        self.assertEqual(groups[0]["betas"], (0.9, 0.95))
        self.assertEqual(groups[0]["weight_decay"], 0.1)
        self.assertEqual(groups[1]["weight_decay"], 0.0)


class TestCliReportsExplicitOptimizerFlags(unittest.TestCase):
    """main() must tell train() which optimizer flags were actually passed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name)
        cfg = tiny_cfg(self.train_bin, self.tmp.name)
        model = from_config(cfg)
        self.ckpt = os.path.join(self.tmp.name, "step_3.pt")
        save_checkpoint(self.ckpt, model, build_optimizer(model, cfg), 3, cfg)
        self.captured = []

    def run_main(self, *argv):
        def record(cfg, resume_from=None, start_step=None, stride=1,
                   device_spec="auto", explicit_optimizer_fields=None):
            self.captured.append((resume_from, set(explicit_optimizer_fields or ())))
        saved = sys.argv
        sys.argv = ["train.py", "--resume", self.ckpt, *argv]
        try:
            with mock.patch("src.train.train", record):
                train_main()
        finally:
            sys.argv = saved

    def test_only_passed_optimizer_flags_are_explicit(self):
        self.run_main("--beta1", "0.8", "--weight-decay", "0.05")
        self.assertEqual(len(self.captured), 1)
        resume_from, explicit = self.captured[0]
        self.assertEqual(resume_from, self.ckpt)
        self.assertEqual(explicit, {"beta1", "weight_decay"})

    def test_no_optimizer_flags_passes_an_empty_override_set(self):
        self.run_main()
        self.assertEqual(self.captured[0][1], set())


if __name__ == "__main__":
    unittest.main()
