"""Offscreen render of the painted diff pane widget (gui/diffview.py)."""

from __future__ import annotations

import unittest

from tests.qt_display import qt_app, requires_qt

SAMPLE = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
-a = 1
+a = 2 # """ + "x" * 400 + """
 b = 2
@@ -9,1 +9,2 @@
 c = 3
+d = 4
"""


@requires_qt
class DiffViewRenderTests(unittest.TestCase):
    def setUp(self):
        qt_app()
        from agentops.gui.diffview import ADD_BAR, DEL_BG, DiffView

        self.colors = ADD_BAR, DEL_BG
        self.view = DiffView()
        self.view.resize(420, 300)
        self.view.show()

    def _pixels(self) -> set[str]:
        image = self.view.grab().toImage()
        return {
            image.pixelColor(x, y).name()
            for x in range(0, image.width(), 3)
            for y in range(0, image.height(), 3)
        }

    def test_paints_tinted_rows_bars_and_no_horizontal_scroll(self):
        self.view.set_diff_text(SAMPLE, truncated=True)
        self.assertEqual([(f.path, f.removed, f.added) for f in self.view.files()], [("app.py", 1, 2)])
        self.assertTrue(set(self.colors) <= self._pixels())
        self.assertFalse(self.view.horizontalScrollBar().isVisible())
        # header + 3 rows + gap + 2 rows + truncation note
        self.assertEqual(self.view.widget().minimumHeight(), 32 + 7 * 20)

    def test_header_click_collapses_and_signals(self):
        self.view.set_diff_text(SAMPLE)
        seen = []
        self.view.fileToggled.connect(lambda path, collapsed: seen.append((path, collapsed)))
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        QTest.mouseClick(self.view.widget(), Qt.MouseButton.LeftButton, pos=QPoint(100, 10))
        self.assertEqual(seen, [("app.py", True)])
        self.assertEqual(self.view.widget().minimumHeight(), 32)
        self.view.set_collapsed("app.py", False)
        self.assertEqual(seen[-1], ("app.py", False))

    def test_stat_payload_shows_summary_without_tints(self):
        self.view.set_payload({"source": "stat", "text": "1 file changed, 1 insertion(+)"})
        self.assertEqual(self.view.files(), [])
        self.assertFalse(set(self.colors) & self._pixels())
        self.assertEqual(self.view.widget().minimumHeight(), 2 * 20)


if __name__ == "__main__":
    unittest.main()
