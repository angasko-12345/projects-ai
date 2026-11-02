"""Qt test support: offscreen QApplication for desktop-client tests.

Widget-level tests (``@requires_qt``) run only when the optional PySide6
dependency (the ``desktop`` extra) is installed, and always on the offscreen
platform so headless CI and local runs behave identically. Controller-level
logic stays display-free and never imports Qt.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
except ImportError:  # optional desktop dependency not installed
    QCoreApplication = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]

requires_qt = unittest.skipIf(QApplication is None, "PySide6 desktop dependency not installed")

_app: object | None = None


def qt_app():
    """Process-wide QApplication, created once on the offscreen platform."""
    global _app
    if QApplication is None:
        raise unittest.SkipTest("PySide6 desktop dependency not installed")
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def _spin(milliseconds: int) -> None:
    """Run the event loop for ``milliseconds`` with the GIL released.

    ``QTest.qWait`` keeps the GIL while it spins (PySide6 6.12), so a
    ``ControllerBridge`` worker only runs between waits and every SQLite call
    it makes costs up to one wait step: a controller read that opens a
    StateStore could take seconds. ``QEventLoop.exec`` releases the GIL like
    ``app.exec()`` does in the real GUI; the DeferredDelete flush keeps qWait's
    ``deleteLater`` behaviour.
    """
    qt_app()
    loop = QEventLoop()
    QTimer.singleShot(max(0, int(milliseconds)), loop.quit)
    loop.exec()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait(milliseconds: int = 30) -> None:
    """Spin the event loop so queued signal deliveries and timers run."""
    _spin(milliseconds)


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
        _spin(step)
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
