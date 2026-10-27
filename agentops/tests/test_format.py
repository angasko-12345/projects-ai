"""Regression tests for Qt-free presentation helpers in gui/format.py."""

from __future__ import annotations

import unittest

from agentops.gui.format import short_id


class ShortIdRegressions(unittest.TestCase):
    def test_short_ids_pass_through(self):
        self.assertEqual(short_id("abc123", 8), "abc123")
        self.assertEqual(short_id("", 8), "")

    def test_long_uuid_truncates_to_length(self):
        value = "5a848b1e9c2d4f6a8b1e9c2d4f6a8b1e9"
        self.assertEqual(short_id(value, 8), value[:8])

    def test_prefixed_ids_keep_their_distinguishing_suffix(self):
        # Failure rows carry short sequential ids ("failure-0", ...). Cutting
        # them to the column length left every row reading "failure-", so
        # rows could not be told apart.
        self.assertEqual(short_id("failure-0", 8), "failure-0")
        self.assertEqual(short_id("failure-12", 8), "failure-12")
        self.assertEqual(short_id("failure-1234", 8), "failure-1234")

    def test_far_over_limit_still_truncates(self):
        self.assertEqual(short_id("failure-12345", 8), "failure-")

    def test_tail_mode_keeps_the_distinguishing_suffix(self):
        # ``failure-`` prefixed ids all share a head; the unique part is the
        # tail, so failure tables render the trailing characters instead.
        self.assertEqual(short_id("failure-71f34b4e9d2a", 8, tail=True), "4b4e9d2a")
        self.assertEqual(short_id("abc", 8, tail=True), "abc")
        self.assertEqual(short_id("", 8, tail=True), "")


if __name__ == "__main__":
    unittest.main()
