"""Tokenizer tests: train a tiny BPE on the fly (CPU only, no data files needed)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tokenizer import (
    MIN_VOCAB_SIZE,
    SPECIAL_TOKENS,
    decode,
    encode,
    load_tokenizer,
    train_bpe_tokenizer,
)

TINY_CORPUS = [
    "hello world hello mini llm\n",
    "the quick brown fox jumps over the lazy dog\n",
    "causal language modeling predicts the next token\n",
]


class TestTokenizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile

        cls.tmp = tempfile.TemporaryDirectory()
        cls.corpus_path = os.path.join(cls.tmp.name, "corpus.txt")
        with open(cls.corpus_path, "w", encoding="utf-8") as f:
            f.writelines(TINY_CORPUS)
        cls.tok_path = os.path.join(cls.tmp.name, "tokenizer.json")
        cls.tok = train_bpe_tokenizer(
            [cls.corpus_path], cls.tok_path, vocab_size=512, min_frequency=1
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_loading(self):
        tok = load_tokenizer(self.tok_path)
        self.assertGreater(tok.get_vocab_size(), 0)

    def test_special_tokens_present(self):
        for t in SPECIAL_TOKENS:
            self.assertIsNotNone(self.tok.token_to_id(t), f"missing {t}")

    def test_encode_decode_roundtrip(self):
        text = "hello world"
        ids = encode(self.tok, text)
        self.assertTrue(len(ids) > 0)
        self.assertEqual(decode(self.tok, ids).strip(), text)

    def test_missing_file_names_the_path(self):
        missing = os.path.join(self.tmp.name, "absent.json")
        with self.assertRaises(FileNotFoundError) as ctx:
            load_tokenizer(missing)
        message = str(ctx.exception)
        self.assertIn(os.path.abspath(missing), message)
        self.assertIn("prepare_data.py", message)

    def test_ids_within_vocab(self):
        vocab = self.tok.get_vocab_size()
        ids = encode(self.tok, "the quick brown fox")
        for i in ids:
            self.assertGreaterEqual(i, 0)
            self.assertLess(i, vocab)

    def test_special_token_ids_are_fixed(self):
        for i, t in enumerate(SPECIAL_TOKENS):
            self.assertEqual(self.tok.token_to_id(t), i)

    def test_unseen_characters_round_trip_without_unk(self):
        # TINY_CORPUS is ASCII-only, so none of these bytes were seen in training.
        # Without the full byte alphabet they encoded to <unk> and were lost.
        unk = self.tok.token_to_id("<unk>")
        for text in ("caf\u00e9 \u00fcn\u00efcode \U0001F680\ttab\n\nx",
                     "\u65e5\u672c\u8a9e \u00a9 \x00\x7f"):
            ids = encode(self.tok, text)
            self.assertNotIn(unk, ids, text)
            self.assertEqual(decode(self.tok, ids), text)

    def test_vocab_holds_the_full_byte_alphabet(self):
        self.assertGreaterEqual(self.tok.get_vocab_size(), MIN_VOCAB_SIZE)
        self.assertEqual(MIN_VOCAB_SIZE, 256 + len(SPECIAL_TOKENS))

    def test_vocab_below_byte_alphabet_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            train_bpe_tokenizer([self.corpus_path],
                                os.path.join(self.tmp.name, "small.json"),
                                vocab_size=MIN_VOCAB_SIZE - 1, min_frequency=1)
        self.assertIn(str(MIN_VOCAB_SIZE), str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "small.json")))


if __name__ == "__main__":
    unittest.main()
