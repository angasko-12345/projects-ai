import unittest

from token_tracker.format import cost_summary, human_count, human_cost, human_tokens


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


class CostSummaryTest(unittest.TestCase):
    def test_empty_when_no_event_reports_cost(self):
        self.assertEqual(cost_summary({"cost_events": 0, "cost": 0.0}), "")
        self.assertEqual(cost_summary({}), "")

    def test_reports_money_and_event_coverage(self):
        totals = {"cost_events": 12, "cost": 4.2537}
        self.assertEqual(cost_summary(totals), " · cost $4.25 across 12 events")
        self.assertEqual(cost_summary({"cost_events": 1, "cost": 0.0}), " · cost $0.00 across 1 event")


if __name__ == "__main__":
    unittest.main()
