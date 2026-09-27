"""Tokenizer tests: train a tiny BPE on the fly (CPU only, no data files needed)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tokenizer import SPECIAL_TOKENS, decode, encode, load_tokenizer, train_bpe_tokenizer

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
            [cls.corpus_path], cls.tok_path, vocab_size=64, min_frequency=1
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

    def test_ids_within_vocab(self):
        vocab = self.tok.get_vocab_size()
        ids = encode(self.tok, "the quick brown fox")
        for i in ids:
            self.assertGreaterEqual(i, 0)
            self.assertLess(i, vocab)


if __name__ == "__main__":
    unittest.main()
