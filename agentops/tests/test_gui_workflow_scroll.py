"""Regression: the Workflows control center keeps its stage cards readable.

The control-center body had no scroll area and gave the StageFlow a stretch
factor, so in a short window the layout squeezed every stage card to a sliver
and clipped its stage name, agent and duration lines to nothing. The body now
scrolls and the cards keep their natural height.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController


@requires_qt
class WorkflowControlCenterScrollTest(unittest.TestCase):
    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(FakeController(), settings=settings)
        self.addCleanup(self._teardown)
        # The compact minimum window is where the crush was worst.
        self.window.resize(960, 620)
        self.window.show()
        wait(50)

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch(
            "agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            destroy(self.window)

    def test_stage_cards_keep_their_height_in_a_short_window(self):
        from PySide6.QtWidgets import QLabel, QScrollArea

        from agentops.gui.widgets import StageNode

        view = self.window._views["workflows"]
        self.window.navigate("workflows")
        self.assertTrue(wait_until(lambda: view._list.proxy.rowCount() == 1))
        view._list.select_row_index(0)
        self.assertTrue(wait_until(lambda: bool(view._flow.findChildren(StageNode))))

        def settled() -> bool:
            # Layout passes are queued; under load the first ones can lag, so
            # poll until the cards reach their natural height (a crushed card
            # never does, and the assertions below then name it).
            nodes = view._flow.findChildren(StageNode)
            return bool(nodes) and all(
                node.height() >= node.minimumSizeHint().height() for node in nodes
            )

        wait_until(settled, 3000)
        wait(50)

        scroll = view._body.findChild(QScrollArea)
        self.assertIsNotNone(scroll, "the control-center body must scroll")
        self.assertTrue(scroll.widget().isAncestorOf(view._flow))

        nodes = view._flow.findChildren(StageNode)
        for node in nodes:
            self.assertGreaterEqual(
                node.height(), node.minimumSizeHint().height(), f"stage {node.key} is crushed"
            )
            for label in node.findChildren(QLabel):
                if label.isVisible() and label.text().strip() and not label.wordWrap():
                    self.assertGreaterEqual(
                        label.height(),
                        label.fontMetrics().height(),
                        f"stage {node.key}: {label.text()!r} is clipped",
                    )


if __name__ == "__main__":
    unittest.main()
