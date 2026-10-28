"""QApplication bootstrap for the AgentOps desktop client."""

from __future__ import annotations

import sys
from pathlib import Path


def run(config_path: str | Path | None = None) -> int:
    """Create the application, main window, and enter the event loop."""
    from PySide6.QtWidgets import QApplication

    from ..gui_controller import AgentOpsController
    from .settings import load_settings
    from .shell import MainWindow
    from .tokens import apply_theme, theme_named

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AgentOps")
    app.setOrganizationName("AgentOps")
    settings = load_settings()
    apply_theme(app, theme_named(settings.theme))
    app.setQuitOnLastWindowClosed(True)

    controller = AgentOpsController(config_path=Path(config_path) if config_path else None)
    window = MainWindow(controller, settings=settings)
    window.restore_geometry()
    window.show()
    return int(app.exec())
