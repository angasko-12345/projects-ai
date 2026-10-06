import unittest

from token_tracker.format import human_count, human_cost, human_tokens


class HumanTokensTest(unittest.TestCase):
    def test_plain_below_thousand(self):
        self.assertEqual(human_tokens(0), "0")
        self.assertEqual(human_tokens(999), "999")

    def test_spec_examples(self):
        self.assertEqual(human_tokens(12_400), "12.4K")
        self.assertEqual(human_tokens(8_700_000), "8.7M")
        self.assertEqual(human_tokens(1_420_000_000), "1.42B")

    def test_rollover_instead_of_1000k(self):
        self.assertEqual(human_tokens(999_999), "1M")

    def test_count_and_cost(self):
        self.assertEqual(human_count(1204), "1,204")
        self.assertEqual(human_cost(None), "-")
        self.assertEqual(human_cost(1.5), "$1.50")


if __name__ == "__main__":
    unittest.main()
