"""WCAG 2.x contrast for the theme tokens (pure Python, no Qt).

Body text in every role (text, muted, faint) must stay AA-readable (4.5:1) on
each surface it is drawn on, primary-button text on its fill, and status
colours (pill text, stat values) on the card surface. Failing pairs found in
the audit: ``text_faint`` on ``surface`` was 3.8:1 and white on ``accent`` 3.2:1.
"""

from __future__ import annotations

import unittest

from agentops.gui.tokens import DARK

AA = 4.5


def relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class ContrastRatioTest(unittest.TestCase):
    def test_formula_matches_known_values(self):
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#000000"), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio("#777777", "#777777"), 1.0, places=2)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#2f62dd"), 5.37, places=2)


class ThemeContrastTest(unittest.TestCase):
    theme = DARK

    def assert_aa(self, foreground_token: str, background_token: str) -> None:
        foreground = getattr(self.theme, foreground_token)
        background = getattr(self.theme, background_token)
        ratio = contrast_ratio(foreground, background)
        self.assertGreaterEqual(
            ratio,
            AA,
            f"{self.theme.name}: {foreground_token} {foreground} on {background_token} "
            f"{background} is {ratio:.2f}:1",
        )

    def test_text_roles_on_every_surface(self):
        for foreground in ("text", "text_muted", "text_faint"):
            for background in ("bg", "surface", "surface_alt"):
                with self.subTest(foreground=foreground, background=background):
                    self.assert_aa(foreground, background)

    def test_on_accent_on_primary_fill(self):
        self.assert_aa("on_accent", "accent_strong")

    def test_status_colors_on_surface(self):
        for status in ("success", "warning", "danger", "orange", "info", "accent"):
            with self.subTest(status=status):
                self.assert_aa(status, "surface")


if __name__ == "__main__":
    unittest.main()
