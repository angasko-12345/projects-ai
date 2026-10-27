"""Regression tests for the token-driven status and severity color lookup.

`agentops.gui.tokens` is deliberately Qt-free: it is plain data plus a QSS
string builder, so it can be imported and tested without the optional
PySide6 dependency. These tests pin the alias table that StatusPillDelegate
paints through, including the severity values that the Failures table renders
as pills alongside lifecycle states.
"""

from __future__ import annotations

import unittest

from agentops.gui.tokens import DARK, status_colors


class StatusColorsTest(unittest.TestCase):
    def test_lifecycle_states_keep_their_colors(self):
        self.assertEqual(status_colors("passed", DARK), (DARK.success, DARK.success))
        self.assertEqual(status_colors("running", DARK), (DARK.accent, DARK.accent))
        self.assertEqual(status_colors("failed", DARK), (DARK.danger, DARK.danger))

    def test_unknown_status_falls_through_to_neutral(self):
        foreground, background = status_colors("brand-new-state", DARK)
        self.assertEqual(foreground, DARK.text_faint)
        self.assertEqual(background, DARK.text_faint)

    def test_severity_values_alias_to_status_tokens(self):
        # The Failures table paints its severity column through the same
        # StatusPillDelegate, so severities need an alias or they render as
        # low-contrast grey indistinguishable from "unknown".
        self.assertEqual(status_colors("critical", DARK), (DARK.danger, DARK.danger))
        self.assertEqual(status_colors("high", DARK), (DARK.danger, DARK.danger))
        self.assertEqual(status_colors("medium", DARK), (DARK.warning, DARK.warning))
        self.assertEqual(status_colors("low", DARK), (DARK.text_muted, DARK.text_muted))

    def test_case_and_whitespace_are_normalized(self):
        self.assertEqual(status_colors("CRITICAL", DARK), (DARK.danger, DARK.danger))
        self.assertEqual(status_colors("  Medium  ", DARK), (DARK.warning, DARK.warning))

    def test_enums_resolve_through_value(self):
        # failure.py uses enum.StrEnum (3.11+); on older interpreters the
        # module import fails, so the assertion is skipped rather than
        # breaking the whole suite on a supported-but-older runtime.
        try:
            from agentops.failure import FailureSeverity
        except ImportError:
            self.skipTest("agentops.failure uses enum.StrEnum (Python 3.11+)")
            return
        self.assertEqual(
            status_colors(FailureSeverity.CRITICAL, DARK),
            (DARK.danger, DARK.danger),
        )


if __name__ == "__main__":
    unittest.main()
