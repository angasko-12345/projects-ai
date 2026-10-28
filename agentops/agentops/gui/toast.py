"""Toast notifications overlaid on the main window.

The host is a small click-safe strip anchored bottom-right of the content
area (``anchor``, 16px inset) - sized to the visible toasts only, so clicks
outside the strip still reach the views beneath. Cards fade+slide in and auto-dismiss; the shell owns the host and
views only call ``ctx.toast(title, message, kind)``.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .tokens import DARK
from .widgets import Badge

TOAST_TIMEOUT_MS = {"info": 4000, "success": 5000, "warning": 7000, "error": 9000}
MAX_VISIBLE_TOASTS = 3
TOAST_INSET = 16  # px from the anchor's right and bottom edges
TOAST_WIDTH = 400
_ENTER_MS = 170
_LEAVE_MS = 140


class _Toast(QFrame):
    """One notification card; calls ``on_close(self)`` when it disappears."""

    def __init__(self, title: str, message: str, kind: str, on_close, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self._on_close = on_close
        self._kind = kind
        self._closing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(DARK.space_md, DARK.space_sm, DARK.space_sm, DARK.space_sm)
        layout.setSpacing(DARK.space_sm)

        column = QVBoxLayout()
        column.setSpacing(DARK.space_xs)
        heading = QLabel(title)
        heading.setProperty("role", "title")
        column.addWidget(heading)
        if message:
            body = QLabel(message)
            body.setWordWrap(True)
            body.setProperty("role", "muted")
            column.addWidget(body)
        layout.addLayout(column, stretch=1)

        status = {
            "success": "passed",
            "error": "failed",
            "warning": "blocked",
            "info": "running",
        }.get(kind, "unknown")
        badge = Badge(status=status)
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        close = QPushButton("x")
        close.setProperty("variant", "ghost")
        close.setFixedWidth(24)
        close.clicked.connect(self.dismiss)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def enter(self) -> None:
        """Fade + small slide-in, then start the auto-dismiss countdown."""
        self._fade.stop()
        self._fade.setDuration(_ENTER_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        position = self.pos()
        self.move(position.x(), position.y() + 12)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(_ENTER_MS)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.setStartValue(self.pos())
        slide.setEndValue(self.pos().__class__(position.x(), position.y()))
        slide.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._timer.start(TOAST_TIMEOUT_MS.get(self._kind, 5000))

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._fade.stop()
        self._fade.setDuration(_LEAVE_MS)
        self._fade.setStartValue(float(self._effect.opacity()))
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish)
        self._fade.start()

    def _finish(self) -> None:
        self._on_close(self)


class ToastHost(QWidget):
    """Bottom-right toast stack; geometry hugs the visible cards only."""

    def __init__(self, parent: QWidget, anchor: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToastHost")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._anchor = anchor if anchor is not None else parent
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(DARK.space_sm)
        self._layout.addStretch(1)
        self._toasts: list[_Toast] = []
        parent.installEventFilter(self)
        if self._anchor is not parent:
            self._anchor.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched in (self.parentWidget(), self._anchor) and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
        ):
            self.reposition()
        return super().eventFilter(watched, event)

    def show_toast(self, title: str, message: str = "", kind: str = "info") -> None:
        kind = kind if kind in TOAST_TIMEOUT_MS else "info"
        toast = _Toast(title, message, kind, on_close=self._remove, parent=self)
        self._toasts.append(toast)
        self._layout.addWidget(toast)
        toast.setFixedWidth(TOAST_WIDTH)
        # A widget added to a layout stays hidden until the event loop runs,
        # and box layouts exclude hidden widgets from size hints and
        # geometry. Showing first keeps reposition() from measuring a host
        # that pretends the new toast is not there (a 24px empty sliver).
        toast.show()
        while len(self._toasts) > MAX_VISIBLE_TOASTS:
            oldest = self._toasts[0]
            self._layout.removeWidget(oldest)
            self._toasts.remove(oldest)
            oldest.deleteLater()
        self.reposition()
        toast.enter()

    def _remove(self, toast: _Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._layout.removeWidget(toast)
        toast.deleteLater()
        self.reposition()

    def reposition(self) -> None:
        """Anchor the strip to the content's bottom-right (zero-size when empty)."""
        parent = self.parentWidget()
        if parent is None:
            return
        if not self._toasts:
            self.setGeometry(parent.width(), parent.height(), 0, 0)
            return
        self.adjustSize()
        width = self.sizeHint().width()
        height = self.sizeHint().height()
        anchor = self._anchor
        origin = anchor.mapTo(parent, QPoint(0, 0)) if anchor is not parent else QPoint(0, 0)
        right = origin.x() + anchor.width() - TOAST_INSET
        bottom = origin.y() + anchor.height() - TOAST_INSET
        self.setGeometry(right - width, bottom - height, width, height)
        self.raise_()
