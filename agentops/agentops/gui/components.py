"""Shell primitives for the v2 design: icon buttons, chips, pills, glyphs.

Everything paints from ``tokens`` (``STATUS_COLOR`` via ``status_color``,
``qcolor`` for translucency) and ``icons``; nothing here reads controller
state. ``paint_pill`` is the one pill painter, shared by :class:`Pill` and
``widgets.StatusPillDelegate`` so table cells and labels look identical.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from .icons import icon, qicon
from .tokens import DARK, Theme, qcolor, qfont, status_color

PILL_HEIGHT = 21
_PILL_PAD = 8

# Status -> Lucide glyph (plan-v2 2.1). Anything else is "not started".
_GLYPHS = {
    "running": "loader-circle",
    "starting": "loader-circle",
    "passed": "circle-check",
    "completed": "circle-check",
    "failed": "circle-x",
    "terminated": "circle-x",
    "timed_out": "timer",
    "blocked": "circle-pause",
}


def _status_key(status: object) -> str:
    return str(getattr(status, "value", status) or "").strip().lower()


def glyph_name(status: object) -> str:
    """Lucide icon name for a status value (enum, str or None)."""
    return _GLYPHS.get(_status_key(status), "circle-dashed")


def pill_text(status: object) -> str:
    """Sentence-case label: ``"timed_out"`` -> ``"Timed out"``, ``"HIGH"`` -> ``"High"``."""
    words = _status_key(status).replace("_", " ").replace("-", " ").split()
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def pill_font(theme: Theme = DARK) -> QFont:
    font = qfont(theme, theme.size_caption)
    font.setWeight(QFont.Weight.Medium)
    return font


def pill_size(text: str, theme: Theme = DARK) -> QSize:
    metrics = QFontMetrics(pill_font(theme))
    return QSize(metrics.horizontalAdvance(text) + 2 * _PILL_PAD, PILL_HEIGHT)


def paint_pill(
    painter: QPainter,
    rect: QRectF,
    text: str,
    color: str,
    theme: Theme = DARK,
    selected_text: QColor | None = None,
) -> QRectF:
    """Paint a fully rounded status pill centred in ``rect``; returns its box.

    Text is ``color``; the fill is ``color`` at 14% and the 1px border at 28%.
    ``selected_text`` (a selected table row) keeps the tint and switches the
    text to the selection's text colour so it stays readable.
    """
    font = pill_font(theme)
    metrics = QFontMetrics(font)
    height = min(float(PILL_HEIGHT), max(0.0, rect.height() - 2))
    label = metrics.elidedText(
        text, Qt.TextElideMode.ElideRight, int(max(0.0, rect.width() - 2 * _PILL_PAD))
    )
    width = min(rect.width(), metrics.horizontalAdvance(label) + 2.0 * _PILL_PAD)
    box = QRectF(0, 0, width, height)
    box.moveCenter(rect.center())
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font)
        painter.setPen(qcolor(color, 0.28))
        painter.setBrush(qcolor(color, 0.14 if selected_text is None else 0.30))
        radius = box.height() / 2
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.setPen(selected_text if selected_text is not None else QColor(color))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
    finally:
        painter.restore()
    return box


class Pill(QLabel):
    """Status or severity pill: 21px, fully rounded, 12px Medium."""

    def __init__(self, status: object = "", parent: QWidget | None = None, theme: Theme = DARK):
        super().__init__(parent)
        self._theme = theme
        self._status = ""
        self.set_status(status)

    def status(self) -> str:
        return self._status

    def color(self) -> str:
        return status_color(self._status, self._theme)

    def set_status(self, status: object) -> None:
        self._status = _status_key(status)
        self.setText(pill_text(status))
        self.setAccessibleName(self.text())
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return pill_size(self.text(), self._theme)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.text():
            return
        painter = QPainter(self)
        paint_pill(painter, QRectF(self.rect()), self.text(), self.color(), self._theme)
        painter.end()


class IconButton(QToolButton):
    """Square, borderless icon button with tooltip, accessible name and badge."""

    def __init__(
        self,
        name: str,
        tooltip: str = "",
        accessible_name: str = "",
        parent: QWidget | None = None,
        *,
        size: int = 32,
        icon_size: int = 18,
        color: str | None = None,
        active_color: str | None = None,
        theme: Theme = DARK,
    ):
        super().__init__(parent)
        self._theme = theme
        self._color = color or theme.text_faint
        self._active_color = active_color or theme.text
        self._icon_px = icon_size
        self._badge = 0
        self._name = ""
        self.setProperty("iconButton", True)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible_name or tooltip.split("  ")[0])
        self.set_icon(name)

    def icon_name(self) -> str:
        return self._name

    def set_icon(self, name: str) -> None:
        self._name = name
        self.setIcon(qicon(name, self._color, self._icon_px, checked_color=self._active_color))

    def badge(self) -> int:
        return self._badge

    def set_badge(self, count: int) -> None:
        """Small danger-filled count on the top-right corner; 0 hides it."""
        count = max(0, int(count or 0))
        if count == self._badge:
            return
        self._badge = count
        self.setAccessibleDescription(f"{count} need attention" if count else "")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        if not self._badge:
            return
        text = str(self._badge) if self._badge < 100 else "99+"
        font = qfont(self._theme, 9)
        font.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        side = 14.0
        width = max(side, metrics.horizontalAdvance(text) + 6.0)
        box = QRectF(self.width() - width - 1, 1, width, side)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._theme.danger))
        painter.drawRoundedRect(box, side / 2, side / 2)
        painter.setPen(QColor(self._theme.on_accent))
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class StatusGlyph(QLabel):
    """Status icon (loader, check, x, timer, pause, dashed) in the status colour."""

    def __init__(
        self, status: object = "", size: int = 14, parent: QWidget | None = None, theme=DARK
    ):
        super().__init__(parent)
        self._theme = theme
        self._px = size
        self._status = ""
        self.setFixedSize(size, size)
        self.set_status(status)

    def status(self) -> str:
        return self._status

    def set_status(self, status: object) -> None:
        self._status = _status_key(status)
        self.setAccessibleName(pill_text(status) or "Pending")
        self._render()

    def _render(self) -> None:
        color = status_color(self._status, self._theme)
        self.setPixmap(icon(glyph_name(self._status), color, self._px, self.devicePixelRatioF()))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._render()  # the window's screen (and its ratio) is known now


class Chip(QFrame):
    """Rounded control-style chip: optional icon, text, optional chevron."""

    def __init__(
        self,
        text: str,
        icon_name: str | None = None,
        chevron: bool = False,
        parent: QWidget | None = None,
        theme: Theme = DARK,
    ):
        super().__init__(parent)
        self.setProperty("chip", True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.space_sm, theme.space_xs, theme.space_sm, theme.space_xs)
        layout.setSpacing(theme.space_sm - 2)
        if icon_name:
            mark = QLabel()
            mark.setPixmap(icon(icon_name, theme.text_muted, 14))
            layout.addWidget(mark)
        self._label = QLabel(text)
        self._label.setProperty("role", "chip")
        layout.addWidget(self._label)
        if chevron:
            arrow = QLabel()
            arrow.setPixmap(icon("chevron-down", theme.text_faint, 12))
            layout.addWidget(arrow)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._label.text()

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.setAccessibleName(text)


class SectionLabel(QWidget):
    """Small-caps section title led by a painted four-diamond mark."""

    _MARK = 10

    def __init__(self, text: str, parent: QWidget | None = None, theme: Theme = DARK):
        super().__init__(parent)
        self._theme = theme
        self._text = text
        self.setAccessibleName(text)
        self.setFixedHeight(18)

    def text(self) -> str:
        return self._text

    def _font(self) -> QFont:
        font = qfont(self._theme, self._theme.size_micro)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        return font

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        advance = QFontMetrics(self._font()).horizontalAdvance(self._text.upper())
        return QSize(self._MARK + 8 + advance + 2, 18)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._theme.text_faint))
        middle = self.height() / 2
        half = self._MARK / 2
        size = 2.1  # half-diagonal of each diamond
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            cx, cy = half + dx * 2.6, middle + dy * 2.6
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx, cy - size),
                        QPointF(cx + size, cy),
                        QPointF(cx, cy + size),
                        QPointF(cx - size, cy),
                    ]
                )
            )
        painter.setPen(QColor(self._theme.text_muted))
        painter.setFont(self._font())
        text_box = QRectF(self._MARK + 8, 0, self.width() - self._MARK - 8, self.height())
        painter.drawText(
            text_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text.upper()
        )
        painter.end()


__all__ = [
    "Chip",
    "IconButton",
    "PILL_HEIGHT",
    "Pill",
    "SectionLabel",
    "StatusGlyph",
    "glyph_name",
    "paint_pill",
    "pill_size",
    "pill_text",
]
