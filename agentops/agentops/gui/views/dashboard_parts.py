"""Dashboard lists: the needs-attention table.

Rows are plain dicts from ``gui/derive.py`` painted by a small delegate, so
the list stays a ``QListWidget`` (``count()``/``item()``, tooltips,
activation) while looking like the mockup's table.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QWidget,
)

from ..components import paint_pill, pill_size, pill_text
from ..tokens import DARK, qcolor, qfont

ROW = Qt.ItemDataRole.UserRole
PILL_ROLE = Qt.ItemDataRole.UserRole + 1

# Severity pill colour roles: red on high and critical, as before.
SEVERITY_ROLES = {"critical": "danger", "high": "danger", "medium": "warning", "low": "text_muted"}

# (key, title, fixed width); the workflow column takes what is left.
_COLUMNS = (
    ("problem", "Problem", 140),
    ("severity", "Severity", 96),
    ("agent", "Agent", 96),
    ("next_step", "Next step", 170),
    ("age", "Age", 48),
)
_PAD = 16


def columns(width: int) -> list[tuple[str, str, float, float]]:
    """(key, title, x, width) per visible column at ``width``.

    Next step, then Agent, drop out before the workflow column gets narrower
    than 200px, so the table never scrolls sideways.
    """
    shown = list(_COLUMNS)
    for optional in ("next_step", "agent"):
        if width - 2 * _PAD - sum(w for _k, _t, w in shown) >= 200:
            break
        shown = [c for c in shown if c[0] != optional]
    first = max(120.0, width - 2 * _PAD - sum(w for _k, _t, w in shown))
    out = [("workflow", "Workflow", float(_PAD), first)]
    x = _PAD + first
    for key, title, w in shown:
        out.append((key, title, x, float(w)))
        x += w
    return out


class AttentionHeader(QWidget):
    """Column titles painted with the same geometry as the rows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(34)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setFont(qfont(DARK, DARK.size_caption))
        painter.setPen(QColor(DARK.text_faint))
        for _key, title, x, w in columns(self.width()):
            box = QRectF(x, 0, w - 8, self.height())
            painter.drawText(box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        painter.setPen(QColor(DARK.navy_border))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class _AttentionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        return QSize(option.rect.width(), 44)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt API
        row = index.data(ROW) or {}
        rect = QRectF(option.rect)
        painter.save()
        if option.state & option.state.State_MouseOver:
            painter.fillRect(rect, qcolor(DARK.text, 0.04))
        painter.setPen(QColor(DARK.navy_border))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        font = qfont(DARK, DARK.size_body)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        for key, _title, x, w in columns(int(rect.width())):
            box = QRectF(rect.left() + x, rect.top(), w - 8, rect.height())
            if key == "severity":
                label = pill_text(row.get(key))
                width = min(box.width(), pill_size(label).width() + 2)
                pill = QRectF(box.left(), box.top() + 10, width, box.height() - 20)
                color = getattr(DARK, index.data(PILL_ROLE) or "text_faint")
                paint_pill(painter, pill, label, color)
                painter.setFont(font)
                continue
            painter.setPen(QColor(DARK.text if key == "workflow" else DARK.text_muted))
            text = metrics.elidedText(
                str(row.get(key) or ""), Qt.TextElideMode.ElideRight, int(box.width())
            )
            painter.drawText(box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


def _plain_list(widget: QListWidget) -> None:
    widget.setProperty("plain", True)
    widget.setFrameShape(QListWidget.Shape.NoFrame)
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setMouseTracking(True)


class AttentionTable(QListWidget):
    """Needs-attention rows: workflow, problem, severity pill, agent, next step, age."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        _plain_list(self)
        self.setItemDelegate(_AttentionDelegate(self))
        self.setUniformItemSizes(True)

    def set_rows(self, rows: Sequence[dict]) -> None:
        self.clear()
        for row in rows:
            item = QListWidgetItem(f"{row['workflow']}: {row['problem']}")
            item.setData(ROW, row)
            item.setData(PILL_ROLE, SEVERITY_ROLES.get(row["severity"], "text_faint"))
            tip = row["error"] or row["problem"]
            item.setToolTip(f"{row['created_at']}\n{tip}" if row["created_at"] else tip)
            self.addItem(item)
        self.setFixedHeight(44 * len(rows))

    def pill_role(self, item: QListWidgetItem) -> str:
        """The severity pill's colour role (``danger``, ``warning``, ...)."""
        return str(item.data(PILL_ROLE) or "")


__all__ = ["AttentionHeader", "AttentionTable", "columns"]
