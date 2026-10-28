"""QApplication bootstrap for the AgentOps desktop client."""

from __future__ import annotations

import sys
from pathlib import Path


def run(config_path: str | Path | None = None) -> int:
    """Create the application, main window, and enter the event loop."""
    from PySide6.QtWidgets import QApplication

    from ..gui_controller import AgentOpsController
    from .shell import MainWindow
    from .tokens import DARK, apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AgentOps")
    app.setOrganizationName("AgentOps")
    apply_theme(app, DARK)
    app.setQuitOnLastWindowClosed(True)

    controller = AgentOpsController(config_path=Path(config_path) if config_path else None)
    window = MainWindow(controller)
    window.restore_geometry()
    window.show()
    return int(app.exec())
