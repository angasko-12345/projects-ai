"""Qt test support: offscreen QApplication for desktop-client tests.

Mirrors ``tests/tk_display.py``: widget-level tests run only when the
optional PySide6 dependency is installed, and always on the offscreen
platform so headless CI and local runs behave identically. Controller-level
logic stays display-free and never imports Qt.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # optional desktop dependency not installed
    QCoreApplication = None  # type: ignore[assignment]
    QTest = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]

requires_qt = unittest.skipIf(
    QApplication is None, "PySide6 desktop dependency not installed"
)

_app: object | None = None


def qt_app():
    """Process-wide QApplication, created once on the offscreen platform."""
    global _app
    if QApplication is None:
        raise unittest.SkipTest("PySide6 desktop dependency not installed")
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def wait(milliseconds: int = 30) -> None:
    """Spin the event loop so queued signal deliveries and timers run."""
    qt_app()
    QTest.qWait(milliseconds)


def wait_until(predicate, timeout_ms: int = 2000) -> bool:  # type: ignore[no-untyped-def]
    """Pump events until ``predicate()`` turns true; returns its outcome.

    Async submits arrive on worker threads; tests poll instead of sleeping a
    fixed amount, which keeps them fast and less timing-sensitive.
    """
    qt_app()
    elapsed = 0
    step = 20
    while not predicate():
        if elapsed >= timeout_ms:
            return bool(predicate())
        QTest.qWait(step)
        elapsed += step
    return True


def destroy(widget) -> None:
    """Tear a widget down defensively (tests must not leak windows)."""
    if widget is None:
        return
    try:
        widget.close()
        widget.deleteLater()
    except RuntimeError:
        pass
    qt_app().processEvents()


def make_context(controller: object, repository: str = r"C:\repo", **overrides):
    """A ``ViewContext`` wired to ``controller`` with recording stubs.

    Panels and views need the full context; tests override only the seams they
    assert on. Toasts are recorded on ``context.bridge.toasts`` so error
    reporting stays assertable without opening real notification cards.
    """
    from agentops.gui.bridge import ControllerBridge
    from agentops.gui.context import ViewContext
    from agentops.gui.settings import AppSettings

    qt_app()  # QObject construction requires an application
    recorded: list[tuple[str, str, str]] = []

    def toast(title: str, message: str = "", kind: str = "info") -> None:
        recorded.append((str(title), str(message), str(kind)))

    bridge = ControllerBridge(controller)
    bridge.toasts = recorded  # type: ignore[attr-defined]
    return ViewContext(
        bridge=bridge,
        repository=lambda: repository,
        navigate=lambda view_id: None,
        toast=toast,
        settings=AppSettings,
        open_task=lambda workflow_id, task_id="": None,
        open_run=lambda run_id: None,
        operation_active=lambda: False,
        update_settings=lambda updates: None,
        **overrides,
    )
