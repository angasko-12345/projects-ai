"""The workspace composer: options, footer chips and the start_task hand-off."""

from __future__ import annotations

import unittest
from dataclasses import replace

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, make_context, qt_app, requires_qt, wait_until
    from tests.test_gui_qt import PROFILES, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, make_context, qt_app, requires_qt, wait_until
    from test_gui_qt import PROFILES, FakeController


@requires_qt
class ComposerTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.composer import Composer

        qt_app()
        self.received: list[tuple] = []
        context = make_context(FakeController(), repository="/work/demo-service")
        context = replace(context, start_task=lambda *args: self.received.append(args) or True)
        self.composer = Composer(context)
        self.composer.resize(700, 160)
        self.composer.show()
        self.addCleanup(destroy, self.composer)

    def test_options_placeholder_and_default_profile(self):
        composer = self.composer
        composer.set_repository("/work/demo-service")
        self.assertEqual(
            composer._input.placeholderText(),
            "Describe a new task for demo-service…  (Ctrl+Enter to start)",
        )
        self.assertTrue(wait_until(lambda: composer._agents_loaded and composer._options_loaded))
        available = [p["identifier"] for p in PROFILES if p["availability"]]
        agents = [composer._agent.itemData(i) for i in range(composer._agent.count())]
        self.assertEqual(agents, [None, *available])
        self.assertEqual(composer._agent.itemText(0), "Auto routing")
        self.assertEqual(composer._verification.currentData(), "default")

    def test_ctrl_enter_starts_and_empty_is_rejected_inline(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        composer = self.composer
        self.assertTrue(wait_until(lambda: composer._options_loaded))
        composer._submit()
        self.assertTrue(composer._warning.isVisible())
        self.assertFalse(composer._send.isEnabled())
        composer._verification.setCurrentIndex(composer._verification.findData("strict"))
        composer._input.setPlainText("  add retries  ")
        self.assertFalse(composer._warning.isVisible())
        QTest.keyClick(composer._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.received, [("add retries", "/work/demo-service", None, "strict")])
        self.assertEqual(composer._input.toPlainText(), "")

    def test_footer_chips_show_the_worktree_ref(self):
        composer = self.composer
        composer.set_worktree(
            {"path": "/wt/wf-1", "branch": "agentops/wf-1", "base_branch": "main"}
        )
        self.assertEqual(composer._branch_chip.text(), "agentops/wf-1")
        self.assertEqual(composer._base_chip.text(), "base main")
        self.assertEqual(composer._worktree_chip.toolTip(), "/wt/wf-1")
        self.assertFalse(composer._branch_chip.isHidden())
        composer.set_worktree({})
        self.assertTrue(composer._branch_chip.isHidden())
        self.assertTrue(composer._base_chip.isHidden())


if __name__ == "__main__":
    unittest.main()
