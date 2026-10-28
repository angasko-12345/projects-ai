"""Lucide icon rendering and the Phase B shell primitives (components.py)."""

from __future__ import annotations

import unittest

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import qt_app, requires_qt
except ModuleNotFoundError:
    from qt_display import qt_app, requires_qt


def _opaque_pixels(pixmap):
    image = pixmap.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                yield color


@requires_qt
class IconTest(unittest.TestCase):
    def setUp(self):
        qt_app()

    def test_icon_recolours_current_color(self):
        from agentops.gui.icons import icon

        for hex_color in ("#3fb950", "#f2615a"):
            pixmap = icon("circle-check", hex_color, 16, 1.0)
            sample = next(_opaque_pixels(pixmap), None)
            self.assertIsNotNone(sample, "icon rendered no opaque stroke pixel")
            self.assertEqual(sample.name(), hex_color)

    def test_icon_renders_at_device_pixel_ratio(self):
        from agentops.gui.icons import icon

        crisp = icon("settings", "#ececf1", 18, 1.5)
        self.assertEqual(crisp.width(), 27)  # 18 logical px at 150%
        self.assertAlmostEqual(crisp.devicePixelRatio(), 1.5)
        self.assertIs(crisp, icon("settings", "#ececf1", 18, 1.5))  # cached

    def test_every_rail_icon_is_bundled(self):
        from agentops.gui.icons import icon_path

        for name in (
            "layout-dashboard",
            "list-checks",
            "workflow",
            "bot",
            "play",
            "shield-check",
            "triangle-alert",
            "git-fork",
            "package",
            "settings",
            "command",
            "panel-left",
        ):
            self.assertTrue(icon_path(name).is_file(), name)

    def test_unknown_icon_raises(self):
        from agentops.gui.icons import icon

        with self.assertRaises(KeyError):
            icon("no-such-icon", "#ffffff")


@requires_qt
class ComponentTest(unittest.TestCase):
    def setUp(self):
        qt_app()

    def test_pill_uses_status_colour_and_sentence_case(self):
        from agentops.gui.components import PILL_HEIGHT, Pill
        from agentops.gui.tokens import DARK

        pill = Pill("timed_out")
        self.assertEqual(pill.text(), "Timed out")
        self.assertEqual(pill.color(), DARK.orange)
        self.assertEqual(pill.sizeHint().height(), PILL_HEIGHT)
        pill.set_status("HIGH")  # FailureSeverity values are upper-case
        self.assertEqual((pill.text(), pill.color()), ("High", DARK.orange))

    def test_pill_paints_translucent_tint_and_coloured_text(self):
        from PySide6.QtGui import QColor, QImage, QPainter

        from agentops.gui.components import paint_pill
        from agentops.gui.tokens import DARK

        image = QImage(120, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor(DARK.surface))
        painter = QPainter(image)
        box = paint_pill(painter, image.rect().toRectF(), "Failed", DARK.danger)
        painter.end()
        self.assertEqual(round(box.height()), 21)
        # Inside the pill, left of the text: the 14% danger tint over surface.
        inside = image.pixelColor(int(box.left()) + 4, int(box.center().y()))
        surface = QColor(DARK.surface)
        self.assertGreater(inside.red(), surface.red())
        self.assertLess(inside.red(), QColor(DARK.danger).red())

    def test_glyph_names_follow_the_status_map(self):
        from agentops.gui.components import StatusGlyph, glyph_name

        self.assertEqual(glyph_name("running"), "loader-circle")
        self.assertEqual(glyph_name("PASSED"), "circle-check")
        self.assertEqual(glyph_name("terminated"), "circle-x")
        self.assertEqual(glyph_name("timed_out"), "timer")
        self.assertEqual(glyph_name("blocked"), "circle-pause")
        self.assertEqual(glyph_name(None), "circle-dashed")
        glyph = StatusGlyph("failed")
        self.assertFalse(glyph.pixmap().isNull())
        self.assertEqual(glyph.accessibleName(), "Failed")

    def test_icon_button_badge_and_accessibility(self):
        from agentops.gui.components import IconButton

        button = IconButton("triangle-alert", "Failures  Ctrl+7")
        self.assertEqual(button.accessibleName(), "Failures")
        self.assertEqual(button.toolTip(), "Failures  Ctrl+7")
        self.assertFalse(button.icon().isNull())
        button.set_badge(5)
        self.assertEqual(button.badge(), 5)
        self.assertEqual(button.accessibleDescription(), "5 need attention")
        button.set_badge(0)
        self.assertEqual(button.accessibleDescription(), "")

    def test_chip_and_section_label_keep_their_text(self):
        from agentops.gui.components import Chip, SectionLabel

        chip = Chip("Auto routing", "bot", chevron=True)
        self.assertEqual(chip.text(), "Auto routing")
        section = SectionLabel("This week")
        self.assertEqual(section.text(), "This week")
        self.assertGreater(section.sizeHint().width(), 40)


if __name__ == "__main__":
    unittest.main()
