"""Bits per byte: the tokenizer-independent validation number.

Pinned here: the byte-length table matches what the text actually encodes to,
bpb is the hand-computable nll / (ln 2 * bytes) on a known case, a ragged final
batch is weighted by the targets it has, the same checkpoint gives the same bpb
twice, and the number train() records equals what `python -m src.evaluate`
recomputes from the checkpoint.
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest

import torch
from tokenizers.pre_tokenizers import ByteLevel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import evaluate as ev
from src.config import Config, load_data_meta
from src.tokenizer import load_tokenizer
from src.train import load_model, read_checkpoint, train
from tests.test_grad_accum import build_data


class UniformModel(torch.nn.Module):
    """Uniform logits over `vocab` ids: every target costs exactly ln(vocab) nats."""

    def __init__(self, vocab: int):
        super().__init__()
        self.vocab = vocab
        self.anchor = torch.nn.Parameter(torch.zeros(1))

    def forward(self, x, y=None):
        logits = torch.zeros(*x.shape, self.vocab) + 0 * self.anchor
        loss = None
        if y is not None:
            loss = torch.nn.functional.cross_entropy(logits.reshape(-1, self.vocab), y.reshape(-1))
        return logits, loss


class ListLoader(list):
    """A list of (x, y) batches with len(), which is all evaluate_stats needs."""


class TestByteTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_data(cls.tmp.name, context_length=16)
        cls.tok = load_tokenizer(os.path.join(cls.tmp.name, "tokenizer.json"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_byte_decoder_is_the_bytelevel_alphabet(self):
        decoder = ev._byte_decoder()
        self.assertEqual(set(decoder), set(ByteLevel.alphabet()))
        self.assertEqual(sorted(decoder.values()), list(range(256)))

    def test_table_sums_to_the_utf8_length_of_the_text(self):
        table = ev.token_byte_lengths(self.tok)
        for text in (
            "The tiny language model reads tokens.",
            "café ünïcode 🚀\ttab\n\nx",
            "  two  spaces",
            "日本語",
        ):
            with self.subTest(text=text):
                ids = self.tok.encode(text).ids
                self.assertEqual(int(table[torch.tensor(ids)].sum()), len(text.encode("utf-8")))

    def test_special_tokens_count_zero_bytes(self):
        table = ev.token_byte_lengths(self.tok)
        for name in ("<pad>", "<unk>", "<bos>", "<eos>"):
            self.assertEqual(int(table[self.tok.token_to_id(name)]), 0, name)

    def test_table_is_sized_to_the_model_vocab(self):
        size = self.tok.get_vocab_size() + 7
        table = ev.token_byte_lengths(self.tok, size)
        self.assertEqual(table.shape, (size,))
        self.assertEqual(int(table[-7:].sum()), 0)


class TestBitsPerByteArithmetic(unittest.TestCase):
    def test_hand_computed_case(self):
        # 4-id vocab, uniform logits: each target costs ln 4 nats = 2 bits.
        table = torch.tensor([0, 1, 2, 3])
        y = torch.tensor([[1, 2, 3, 3]])  # 1+2+3+3 = 9 bytes, 4 targets -> 8 bits
        loader = ListLoader([(torch.zeros_like(y), y)])
        stats = ev.evaluate_stats(
            UniformModel(4), loader, 1, torch.device("cpu"), byte_lengths=table
        )
        self.assertAlmostEqual(stats["loss"], math.log(4), places=6)
        self.assertEqual(stats["bytes"], 9)
        self.assertEqual(stats["tokens"], 4)
        self.assertAlmostEqual(stats["bpb"], 8 / 9, places=6)
        self.assertAlmostEqual(ev.bits_per_byte(4 * math.log(4), 9), 8 / 9, places=12)

    def test_ragged_final_batch_is_weighted_by_its_targets(self):
        table = torch.tensor([0, 1, 2, 3])
        full = torch.tensor([[1, 1, 1], [1, 1, 1]])  # 6 targets, 6 bytes
        ragged = torch.tensor([[3, 3, 3]])  # 3 targets, 9 bytes
        loader = ListLoader([(torch.zeros_like(full), full), (torch.zeros_like(ragged), ragged)])
        stats = ev.evaluate_stats(
            UniformModel(4), loader, 2, torch.device("cpu"), byte_lengths=table
        )
        self.assertEqual((stats["tokens"], stats["bytes"]), (9, 15))
        self.assertAlmostEqual(stats["bpb"], 9 * 2 / 15, places=6)

    def test_limit_counts_only_the_scored_batches(self):
        table = torch.tensor([0, 1, 2, 3])
        a, b = torch.tensor([[1, 1]]), torch.tensor([[3, 3]])
        loader = ListLoader([(a, a), (b, b)])
        stats = ev.evaluate_stats(
            UniformModel(4), loader, 1, torch.device("cpu"), byte_lengths=table
        )
        self.assertEqual(stats["bytes"], 2)

    def test_zero_bytes_is_nan_not_a_division_error(self):
        self.assertTrue(math.isnan(ev.bits_per_byte(1.0, 0)))

    def test_without_a_table_no_bpb_is_claimed(self):
        y = torch.tensor([[1, 2]])
        stats = ev.evaluate_stats(UniformModel(4), ListLoader([(y, y)]), 1, torch.device("cpu"))
        self.assertNotIn("bpb", stats)


class TestBpbEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.train_bin = build_data(cls.tmp.name, context_length=16)
        cls.ckpt_dir = os.path.join(cls.tmp.name, "ckpts")
        cls.cfg = Config(
            vocab_size=load_data_meta(cls.train_bin)["vocab_size"],
            context_length=16,
            n_layers=1,
            n_heads=2,
            d_model=32,
            d_ff=64,
            batch_size=2,
            max_steps=4,
            warmup_steps=1,
            eval_interval=2,
            eval_batches=3,
            train_bin=cls.train_bin,
            val_bin=os.path.join(cls.tmp.name, "val.bin"),
            tokenizer_path=os.path.join(cls.tmp.name, "tokenizer.json"),
            checkpoint_dir=cls.ckpt_dir,
            seed=0,
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            train(cls.cfg)
        cls.train_log = out.getvalue()
        cls.final = os.path.join(cls.ckpt_dir, "final.pt")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_eval(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()):
            return ev.main(["--checkpoint", self.final, "--device", "cpu", *extra])

    def test_train_logs_and_records_a_finite_bpb(self):
        self.assertIn("val_bpb", self.train_log)
        ckpt = read_checkpoint(self.final)
        self.assertTrue(math.isfinite(ckpt["val_bpb"]))
        self.assertGreater(ckpt["val_bpb"], 0.0)
        self.assertTrue(
            math.isfinite(read_checkpoint(os.path.join(self.ckpt_dir, "step_2.pt"))["val_bpb"])
        )

    def test_evaluate_cli_reproduces_the_training_eval(self):
        # Same windows (stride 1), same batch count: same loss and bpb exactly.
        ckpt = read_checkpoint(self.final)
        result = self.run_eval("--stride", "1", "--batches", str(self.cfg.eval_batches))
        self.assertEqual(result["val_loss"], ckpt["val_loss"])
        self.assertEqual(result["val_bpb"], ckpt["val_bpb"])

    def test_same_checkpoint_same_bpb_twice(self):
        a, b = self.run_eval(), self.run_eval()
        self.assertEqual(a["val_bpb"], b["val_bpb"])
        self.assertEqual(a["val_bytes"], b["val_bytes"])

    def test_default_stride_scores_each_val_token_once(self):
        result = self.run_eval()
        self.assertEqual(result["stride"], self.cfg.context_length)
        n_val = os.path.getsize(self.cfg.val_bin) // 2
        windows = (n_val - self.cfg.context_length - 1) // self.cfg.context_length + 1
        self.assertEqual(result["val_tokens"], windows * self.cfg.context_length)

    def test_sample_sheet_is_seeded_and_leaves_the_rng_alone(self):
        model, _ = load_model(self.final)
        tok = load_tokenizer(self.cfg.tokenizer_path)
        torch.manual_seed(99)
        before = torch.get_rng_state()
        sheet = ev.sample_sheet(model, tok, torch.device("cpu"))
        self.assertTrue(torch.equal(before, torch.get_rng_state()))
        out = os.path.join(self.tmp.name, "eval.json")
        a = self.run_eval("--samples", "--json-out", out)
        b = self.run_eval("--samples")
        self.assertEqual(a["samples"], sheet)
        self.assertEqual(a["samples"], b["samples"])
        self.assertEqual([r["prompt"] for r in a["samples"]], list(ev.SAMPLE_PROMPTS))
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["val_bpb"], a["val_bpb"])


if __name__ == "__main__":
    unittest.main()
