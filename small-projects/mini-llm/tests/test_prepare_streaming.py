"""Streaming prepare_data.py parity tests: same bytes, splits, framing, meta. CPU only."""

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prepare_data
from prepare_data import encode_corpus, split_counts
from src.config import Config, config_for_data, load_data_meta
from src.dataset import TokenDataset, load_token_ids
from src.tokenizer import load_tokenizer, train_bpe_tokenizer

# Exercises blank-line framing edge cases: triple newline, whitespace-only
# piece, internal single newline, trailing spaces, no trailing newline.
CORPUS = (
    "Lily loves tiny stars.\n\n"
    "The moon is round and bright tonight.\n\n\n"
    "   \n\n"
    "Ben has a red ball.\nHe kicks it far.\n\n"
    "Trailing spaces ride along   \n\n"
    "No trailing newline on the last passage."
)
BIG_CORPUS = CORPUS * 4

CONTEXT = 8
VAL_FRAC = 0.2


def run_prepare(tmp: str, corpus_text: str = BIG_CORPUS,
                context_length: int = CONTEXT, val_frac: float = VAL_FRAC,
                extra_argv=()) -> str:
    """Run prepare_data.main() over a temporary corpus; return the train .bin path."""
    corpus = os.path.join(tmp, "corpus.txt")
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(corpus_text)
    argv = sys.argv
    sys.argv = [
        "prepare_data.py",
        "--input", corpus,
        "--tokenizer-out", os.path.join(tmp, "tokenizer.json"),
        "--train-out", os.path.join(tmp, "train.bin"),
        "--val-out", os.path.join(tmp, "val.bin"),
        "--vocab-size", "64",
        "--min-frequency", "1",
        "--val-frac", str(val_frac),
        "--context-length", str(context_length),
    ] + list(extra_argv)
    try:
        prepare_data.main()
    finally:
        sys.argv = argv
    return os.path.join(tmp, "train.bin")


def reference_ids(tmp: str, corpus_text: str = BIG_CORPUS) -> np.ndarray:
    """Small-corpus reference encoding via the kept encode_corpus helper."""
    tok = load_tokenizer(os.path.join(tmp, "tokenizer.json"))
    return np.array(encode_corpus(tok, corpus_text), dtype=np.int64)


def load_bins(tmp: str) -> tuple[np.ndarray, np.ndarray]:
    train = load_token_ids(os.path.join(tmp, "train.bin")).astype(np.int64)
    val = load_token_ids(os.path.join(tmp, "val.bin")).astype(np.int64)
    return np.array(train), np.array(val)


class TestStreamingParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_stream_matches_reference_semantics(self):
        """Byte-identical .bin output to the old whole-file encode + split."""
        train_bin = run_prepare(self.tmp.name)
        train, val = load_bins(self.tmp.name)
        ref = reference_ids(self.tmp.name)
        meta = load_data_meta(train_bin)
        self.assertEqual(len(ref), meta["train_tokens"] + meta["val_tokens"])
        self.assertTrue(np.array_equal(train, ref[:meta["train_tokens"]]))
        self.assertTrue(np.array_equal(val, ref[meta["train_tokens"]:]))
        self.assertEqual(train.dtype, np.int64)  # comparison domain only
        self.assertEqual(load_token_ids(train_bin).dtype, np.uint16)

    def test_train_val_counts(self):
        """Split applies val_frac exactly, with the one-sample val floor."""
        train_bin = run_prepare(self.tmp.name)
        meta = load_data_meta(train_bin)
        total = meta["train_tokens"] + meta["val_tokens"]
        # Corpus is large enough that the fraction branch (not the floor) applies.
        self.assertGreaterEqual(int(total * VAL_FRAC), CONTEXT + 2)
        self.assertEqual(meta["val_tokens"], int(total * VAL_FRAC))
        self.assertEqual(meta["train_tokens"], total - meta["val_tokens"])
        n_train, n_val = split_counts(total, VAL_FRAC, CONTEXT)
        self.assertEqual((n_train, n_val), (meta["train_tokens"], meta["val_tokens"]))
        train, val = load_bins(self.tmp.name)
        self.assertEqual(len(train), meta["train_tokens"])
        self.assertEqual(len(val), meta["val_tokens"])
        self.assertEqual(os.path.getsize(train_bin), meta["train_tokens"] * 2)
        self.assertEqual(
            os.path.getsize(os.path.join(self.tmp.name, "val.bin")),
            meta["val_tokens"] * 2,
        )

    def test_bos_eos_framing(self):
        """Every passage is <bos> ... <eos>; first token <bos>, last <eos>."""
        run_prepare(self.tmp.name)
        tok = load_tokenizer(os.path.join(self.tmp.name, "tokenizer.json"))
        bos, eos = tok.token_to_id("<bos>"), tok.token_to_id("<eos>")
        train, val = load_bins(self.tmp.name)
        full = np.concatenate([train, val])
        n_passages = sum(
            1 for p in BIG_CORPUS.split("\n\n") if p.strip()
        )
        self.assertEqual(full[0], bos)
        self.assertEqual(full[-1], eos)
        self.assertEqual(int((full == bos).sum()), n_passages)
        self.assertEqual(int((full == eos).sum()), n_passages)
        bos_pos = np.nonzero(full == bos)[0]
        self.assertTrue(all(i == 0 or full[i - 1] == eos for i in bos_pos))

    def test_metadata(self):
        """meta.json keys, dtypes, and Config/TokenDataset compatibility."""
        train_bin = run_prepare(self.tmp.name)
        meta = load_data_meta(train_bin)
        self.assertEqual(
            set(meta),
            {"vocab_size", "train_tokens", "val_tokens", "dtype", "context_length"},
        )
        tok = load_tokenizer(os.path.join(self.tmp.name, "tokenizer.json"))
        self.assertEqual(meta["vocab_size"], tok.get_vocab_size())
        self.assertEqual(meta["dtype"], "uint16")
        self.assertEqual(meta["context_length"], CONTEXT)
        cfg = config_for_data(train_bin)
        self.assertIsInstance(cfg, Config)
        self.assertEqual(cfg.vocab_size, meta["vocab_size"])
        ds = TokenDataset(load_token_ids(train_bin), CONTEXT)
        self.assertEqual(len(ds), meta["train_tokens"] - CONTEXT)

    def test_chunk_boundaries_do_not_change_bytes(self):
        """7-char reads split every passage and delimiter; output must not move."""
        for name, value in (("READ_CHARS", 7), ("WRITE_TOKENS", 5),
                            ("COPY_TOKENS", 3)):
            old = getattr(prepare_data, name)
            setattr(prepare_data, name, value)
            self.addCleanup(setattr, prepare_data, name, old)
        train_bin = run_prepare(self.tmp.name)
        train, val = load_bins(self.tmp.name)
        ref = reference_ids(self.tmp.name)
        meta = load_data_meta(train_bin)
        self.assertTrue(np.array_equal(np.concatenate([train, val]), ref))
        self.assertEqual(len(train), meta["train_tokens"])

    def test_main_never_builds_whole_corpus_list(self):
        """main() must succeed with encode_corpus disabled; temp file is removed."""
        train_bin = run_prepare(self.tmp.name)
        before = {
            p: load_token_ids(os.path.join(self.tmp.name, p)).tobytes()
            for p in ("train.bin", "val.bin")
        }
        failing = AssertionError("whole-corpus encode_corpus must not be called")
        old = prepare_data.encode_corpus
        prepare_data.encode_corpus = lambda *a, **k: (_ for _ in ()).throw(failing)
        self.addCleanup(setattr, prepare_data, "encode_corpus", old)
        run_prepare(self.tmp.name, extra_argv=["--skip-training"])
        after = {
            p: load_token_ids(os.path.join(self.tmp.name, p)).tobytes()
            for p in ("train.bin", "val.bin")
        }
        self.assertEqual(before, after)
        leftovers = [p for p in os.listdir(self.tmp.name)
                     if p.startswith(".encode_all_")]
        self.assertEqual(leftovers, [])
        self.assertTrue(os.path.exists(train_bin))

    def test_empty_input_error_preserved(self):
        tiny = os.path.join(self.tmp.name, "tiny.txt")
        with open(tiny, "w", encoding="utf-8") as f:
            f.write("abc def " * 20)
        tok_path = os.path.join(self.tmp.name, "tok.json")
        train_bpe_tokenizer([tiny], tok_path, vocab_size=64, min_frequency=1)
        blank = os.path.join(self.tmp.name, "blank.txt")
        with open(blank, "w", encoding="utf-8") as f:
            f.write("  \n  \n")
        argv = sys.argv
        sys.argv = ["prepare_data.py", "--input", blank,
                    "--tokenizer-out", tok_path,
                    "--train-out", os.path.join(self.tmp.name, "t.bin"),
                    "--val-out", os.path.join(self.tmp.name, "v.bin"),
                    "--min-frequency", "1", "--context-length", "8",
                    "--skip-training"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                prepare_data.main()
        finally:
            sys.argv = argv
        self.assertIn("is empty", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
