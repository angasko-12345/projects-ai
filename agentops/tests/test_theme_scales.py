"""Theme scales and the single-colour status map.

Qt-free: tokens and settings are plain data, so these run without PySide6.
"""

from __future__ import annotations

import dataclasses
import unittest

from agentops.gui.tokens import (
    DARK,
    STATUS_COLOR,
    build_stylesheet,
    css_font_list,
    status_color,
)


class ScalesTest(unittest.TestCase):
    def test_type_scale_in_px_with_legacy_aliases(self):
        scale = (
            DARK.size_micro,
            DARK.size_caption,
            DARK.size_body,
            DARK.size_prose,
            DARK.size_title,
            DARK.size_section,
            DARK.size_page,
            DARK.size_stat,
        )
        self.assertEqual(scale, (11, 12, 13, 14, 15, 18, 22, 28))
        self.assertEqual(DARK.size_small, DARK.size_caption)
        self.assertEqual(DARK.size_base, DARK.size_body)
        self.assertEqual(DARK.size_heading, DARK.size_section)

    def test_spacing_and_radii(self):
        spacing = sorted(
            {
                DARK.space_xs,
                DARK.space_sm,
                DARK.space_md,
                DARK.space_lg,
                DARK.space_page,
                DARK.space_xl,
                DARK.space_xxl,
            }
        )
        self.assertEqual(spacing, [4, 8, 12, 16, 20, 24, 32])
        self.assertEqual((DARK.radius_sm, DARK.radius_md, DARK.radius_lg), (6, 10, 12))
        self.assertGreaterEqual(DARK.radius_pill, 999)

    def test_font_family_fallbacks(self):
        self.assertEqual(DARK.font_families, ("Segoe UI Variable Text", "Segoe UI", "Inter"))
        self.assertEqual(DARK.mono_families, ("Cascadia Mono", "Consolas", "JetBrains Mono"))
        self.assertEqual(DARK.font_family, "Segoe UI Variable Text")
        self.assertEqual(
            css_font_list(DARK.font_families, "sans-serif"),
            '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif',
        )
        self.assertIn(css_font_list(DARK.font_families, "sans-serif"), build_stylesheet(DARK))

    def test_dark_values(self):
        self.assertEqual(
            (DARK.bg, DARK.sidebar, DARK.surface, DARK.surface_alt, DARK.selected),
            ("#0b0c0f", "#0f1014", "#14161a", "#1a1c21", "#23262d"),
        )
        self.assertEqual(
            (DARK.text, DARK.text_muted, DARK.text_faint), ("#ececf1", "#a4a7b0", "#858893")
        )
        self.assertEqual((DARK.accent, DARK.accent_strong), ("#6f9bff", "#2f62dd"))


class StatusColorTest(unittest.TestCase):
    def test_lifecycle_map(self):
        expected = {
            "running": DARK.info,
            "starting": DARK.info,
            "passed": DARK.success,
            "completed": DARK.success,
            "failed": DARK.danger,
            "terminated": DARK.danger,
            "timed_out": DARK.orange,
            "blocked": DARK.warning,
            "pending": DARK.text_faint,
            "cancelled": DARK.text_faint,
        }
        for status, color in expected.items():
            with self.subTest(status=status):
                self.assertEqual(status_color(status), color)

    def test_severities_are_case_normalised(self):
        # FailureSeverity values are upper-case ("HIGH"); a case-sensitive
        # lookup is what painted every severity pill grey in the mockups.
        self.assertEqual(status_color("CRITICAL"), DARK.danger)
        self.assertEqual(status_color("HIGH"), DARK.orange)
        self.assertEqual(status_color(" Medium "), DARK.warning)
        self.assertEqual(status_color("low"), DARK.text_faint)

    def test_enums_unknowns_and_themes(self):
        try:
            from agentops.failure import FailureSeverity
        except ImportError:
            self.skipTest("agentops.failure uses enum.StrEnum (Python 3.11+)")
            return
        self.assertEqual(status_color(FailureSeverity.HIGH), DARK.orange)
        self.assertEqual(status_color(None), DARK.text_faint)
        self.assertEqual(status_color("brand-new-state"), DARK.text_faint)
        self.assertEqual(status_color("timed-out"), DARK.orange)
        self.assertTrue(set(STATUS_COLOR.values()) <= {f.name for f in dataclasses.fields(DARK)})


if __name__ == "__main__":
    unittest.main()
