"""Theme plumbing: palette, Fusion style and transparent scroll surfaces.

Without an application palette every QScrollArea viewport painted the
platform's light Window colour behind the dark theme's light text (a 1.05:1
"light slab"). These tests pin the fix: ``apply_theme`` installs Fusion plus a
palette built from the tokens, and the stylesheet makes scroll areas, their
content widget and page stacks transparent. ``tokens`` itself stays Qt-free.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from agentops.gui.tokens import DARK, build_stylesheet

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import qt_app, requires_qt
except ModuleNotFoundError:
    from qt_display import qt_app, requires_qt

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class StylesheetSurfacesTest(unittest.TestCase):
    def test_scroll_surfaces_are_transparent(self):
        sheet = build_stylesheet(DARK)
        self.assertIn("QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget", sheet)
        block = sheet.split("QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget", 1)[1]
        rule = block.split("}", 1)[0]
        self.assertIn("background: transparent", rule)
        self.assertIn("border: none", rule)

    def test_tokens_import_without_qt(self):
        # A finder that refuses PySide6 proves the module-level import is Qt-free.
        code = (
            "import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('PySide6', 'shiboken6'):\n"
            "            raise ImportError('blocked: ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Block())\n"
            "import agentops.gui.tokens as t\n"
            "assert callable(t.build_palette) and callable(t.apply_theme)\n"
            "print('ok')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PACKAGE_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")


@requires_qt
class PaletteTest(unittest.TestCase):
    def setUp(self):
        qt_app()

    def test_palette_carries_theme_colors(self):
        from PySide6.QtGui import QPalette

        from agentops.gui.tokens import build_palette

        palette = build_palette(DARK)
        role = QPalette.ColorRole
        self.assertEqual(palette.color(role.Window).name(), DARK.bg)
        self.assertEqual(palette.color(role.WindowText).name(), DARK.text)
        self.assertEqual(palette.color(role.Base).name(), DARK.surface)
        self.assertEqual(palette.color(role.PlaceholderText).name(), DARK.text_faint)
        disabled = QPalette.ColorGroup.Disabled
        self.assertEqual(palette.color(disabled, role.Text).name(), DARK.text_faint)
        self.assertEqual(palette.color(disabled, role.ButtonText).name(), DARK.text_faint)

    def test_apply_theme_sets_fusion_palette_font_and_stylesheet(self):
        from agentops.gui.tokens import apply_theme

        calls: dict[str, object] = {}

        class RecordingApp:  # records instead of mutating the shared QApplication
            def __getattr__(self, name):
                return lambda value: calls.__setitem__(name, value)

        apply_theme(RecordingApp(), DARK)
        self.assertEqual(calls["setStyle"], "Fusion")
        self.assertEqual(calls["setPalette"].window().color().name(), DARK.bg)
        self.assertEqual(calls["setStyleSheet"], build_stylesheet(DARK))
        self.assertIn(DARK.font_family, calls["setFont"].family())


if __name__ == "__main__":
    unittest.main()
