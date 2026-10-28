"""Dashboard lists: the needs-attention table, the activity feed, agent bars.

Rows are plain dicts from ``gui/derive.py`` painted by small delegates, so
each list stays a ``QListWidget`` (``count()``/``item()``, tooltips,
activation) while looking like the mockup's table and feed.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QWidget,
)

from ..charts import BarRow
from ..components import paint_pill, pill_size, pill_text
from ..icons import icon
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


class _ActivityDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        return QSize(option.rect.width(), 47)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt API
        row = index.data(ROW) or {}
        rect = QRectF(option.rect)
        color = getattr(DARK, row.get("color") or "text_muted")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        badge = QRectF(rect.left(), rect.top() + 6, 28, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(color, 0.16))
        painter.drawEllipse(badge)
        glyph = icon(row.get("icon") or "activity", color, 14)
        painter.drawPixmap(int(badge.left() + 7), int(badge.top() + 7), glyph)
        left = badge.right() + 14
        age_font = qfont(DARK, DARK.size_caption)
        age_w = QFontMetrics(age_font).horizontalAdvance(row.get("age", "")) + 2
        title_font = qfont(DARK, DARK.size_body)
        title_font.setWeight(QFont.Weight.Medium)
        line = QRectF(left, rect.top() + 4, rect.right() - left - age_w - 8, 18)
        painter.setFont(title_font)
        painter.setPen(QColor(DARK.text))
        elide = Qt.TextElideMode.ElideRight
        painter.drawText(
            line,
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(row.get("title", ""), elide, int(line.width())),
        )
        painter.setFont(age_font)
        painter.setPen(QColor(DARK.text_faint))
        painter.drawText(
            QRectF(rect.right() - age_w, line.top(), age_w, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            row.get("age", ""),
        )
        detail = QRectF(left, line.bottom() + 1, rect.right() - left, 17)
        painter.drawText(
            detail,
            Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(age_font).elidedText(row.get("detail", ""), elide, int(detail.width())),
        )
        painter.restore()


class ActivityList(QListWidget):
    """Activity feed: round type icon, message, age, and a detail line."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        _plain_list(self)
        self.setItemDelegate(_ActivityDelegate(self))
        self.setWordWrap(True)  # the full message stays in the tooltip and item text

    def set_rows(self, rows: Sequence[dict]) -> None:
        self.clear()
        for row in rows:
            item = QListWidgetItem(row["title"])
            item.setData(ROW, row)
            item.setToolTip(row["tooltip"])
            self.addItem(item)
        self.setFixedHeight(47 * len(rows))


class AgentBars(QWidget):
    """Runs per agent over the window, with each agent's success share."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(DARK.space_md)
        self._grid.setVerticalSpacing(DARK.space_sm)
        self._grid.setColumnStretch(1, 1)

    def set_bars(self, bars: Sequence[tuple[str, int, int]]) -> None:
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        top = max((runs for _agent, runs, _ok in bars), default=0)
        for row, (agent, runs, ok) in enumerate(bars):
            name = QLabel(agent)
            self._grid.addWidget(name, row, 0)
            self._grid.addWidget(BarRow(runs, top, DARK.series_2), row, 1)
            count = QLabel(f"{runs} run" + ("" if runs == 1 else "s"))
            count.setProperty("role", "faint")
            self._grid.addWidget(count, row, 2)
            share = QLabel(f"{ok}% ok")
            tone = DARK.success if ok >= 90 else DARK.warning if ok >= 50 else DARK.danger
            share.setStyleSheet(f"color: {tone};")
            self._grid.addWidget(share, row, 3, Qt.AlignmentFlag.AlignRight)


__all__ = ["ActivityList", "AgentBars", "AttentionHeader", "AttentionTable", "columns"]
