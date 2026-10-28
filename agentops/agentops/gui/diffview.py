"""Painted unified diff for the workspace diff pane (not wired into a view yet).

``DiffView`` is a scroll area over one painted canvas: per-file headers
(chevron, file icon, path, -n / +n), one line-number gutter, tinted add/del
rows with a 3px bar, "n unmodified lines" separators, elided long lines and a
truncation note. Feed it ``AgentOpsController.workflow_diff()`` payloads with
``set_payload``; clicking a file header collapses it.
"""

from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from . import tokens
from .diffmodel import DiffRow, FileDiff, parse_unified_diff

ADD_BG, ADD_BAR = "#12261a", "#2ea043"
DEL_BG, DEL_BAR = "#2d1517", "#f85149"
LINE_H, HEADER_H, FONT_PX, BAR_W, FILE_GAP = 20, 32, 12, 3, 12


def _mono_font(theme: tokens.Theme) -> QFont:
    font = QFont()
    families = getattr(theme, "mono_families", None) or theme.font_mono.split(",")
    font.setFamilies([name.strip() for name in families])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(FONT_PX)
    return font


class _Canvas(QWidget):
    def __init__(self, view: DiffView) -> None:
        super().__init__(view)
        self._view = view
        self.items: list[tuple[str, int, object]] = []  # (kind, file index, payload)
        self.tops: list[int] = []

    def layout_items(self) -> None:
        view, self.items, self.tops, y = self._view, [], [], 0
        for kind, index, payload in view._entries():
            if kind == "header" and y:
                y += FILE_GAP  # breathing room between file blocks
            self.items.append((kind, index, payload))
            self.tops.append(y)
            y += HEADER_H if kind == "header" else LINE_H
        self.setMinimumHeight(y)
        self.update()

    def item_at(self, y: float) -> tuple[str, int, object] | None:
        position = bisect_right(self.tops, y) - 1
        return self.items[position] if 0 <= position < len(self.items) else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        item = self.item_at(event.position().y())
        if item and item[0] == "header":
            self._view.toggle_file(item[1])

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        view, t = self._view, self._view._theme
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(t.bg))
        painter.setFont(view._mono)
        metrics = painter.fontMetrics()
        gutter = metrics.horizontalAdvance("0" * max(3, len(str(view._max_number)))) + 16
        first = max(0, bisect_right(self.tops, event.rect().top()) - 1)
        for position in range(first, len(self.items)):
            top = self.tops[position]
            if top > event.rect().bottom():
                break
            kind, index, payload = self.items[position]
            if kind == "header":
                self._paint_header(painter, top, view._files[index], index in view._collapsed)
            else:
                self._paint_row(painter, top, payload, gutter)
        painter.end()

    def _paint_header(self, painter: QPainter, top: int, diff: FileDiff, collapsed: bool) -> None:
        t, width = self._view._theme, self.width()
        painter.fillRect(0, top, width, HEADER_H, QColor(t.surface))
        painter.fillRect(0, top + HEADER_H - 1, width, 1, QColor(t.border))
        mid = top + HEADER_H / 2
        pen = QPen(QColor(t.text_muted), 1.4)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        chevron = QPainterPath()
        if collapsed:  # pointing right
            chevron.moveTo(13, mid - 4), chevron.lineTo(17, mid), chevron.lineTo(13, mid + 4)
        else:  # pointing down
            chevron.moveTo(11, mid - 2), chevron.lineTo(15, mid + 2), chevron.lineTo(19, mid - 2)
        painter.drawPath(chevron)
        icon = QPainterPath()  # file outline with a folded corner
        icon.moveTo(30, mid - 6), icon.lineTo(35, mid - 6), icon.lineTo(39, mid - 2)
        icon.lineTo(39, mid + 6), icon.lineTo(30, mid + 6), icon.closeSubpath()
        painter.drawPath(icon)
        painter.drawLine(QPointF(34, mid - 1), QPointF(36, mid - 1))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        metrics = painter.fontMetrics()
        plus, minus = f"+{diff.added}", f"\u2212{diff.removed}"
        right = width - 12
        plus_x = right - metrics.horizontalAdvance(plus)
        minus_x = plus_x - 10 - metrics.horizontalAdvance(minus)
        box = QRectF(0, top, 0, HEADER_H)
        painter.setPen(QColor(t.success))
        painter.drawText(box.adjusted(plus_x, 0, right, 0), Qt.AlignmentFlag.AlignVCenter, plus)
        painter.setPen(QColor(t.danger))
        painter.drawText(box.adjusted(minus_x, 0, plus_x, 0), Qt.AlignmentFlag.AlignVCenter, minus)
        painter.setFont(self._view._ui)
        path = painter.fontMetrics().elidedText(diff.path, Qt.TextElideMode.ElideMiddle, minus_x - 60)
        painter.setPen(QColor(t.text))
        painter.drawText(box.adjusted(48, 0, minus_x - 8, 0), Qt.AlignmentFlag.AlignVCenter, path)
        painter.setFont(self._view._mono)

    def _paint_row(self, painter: QPainter, top: int, row: DiffRow, gutter: int) -> None:
        t, width = self._view._theme, self.width()
        text_x = BAR_W + gutter + 8
        box = QRectF(0, top, 0, LINE_H)
        middle = Qt.AlignmentFlag.AlignVCenter
        if row.kind in ("gap", "note"):
            label = f"{row.number} unmodified lines" if row.kind == "gap" else row.text
            label_w = painter.fontMetrics().horizontalAdvance(label)
            if row.kind == "note":
                label = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, width - text_x - 8)
                painter.setPen(QColor(t.text_muted))
                painter.drawText(box.adjusted(text_x, 0, width - 8, 0), middle, label)
                return
            left = max(text_x, (width - label_w) / 2)
            painter.fillRect(QRectF(text_x, top + LINE_H / 2, left - text_x - 8, 1), QColor(t.border))
            painter.fillRect(QRectF(left + label_w + 8, top + LINE_H / 2, width - left - label_w - 16, 1),
                             QColor(t.border))
            painter.setPen(QColor(t.text_faint))
            painter.drawText(box.adjusted(left, 0, left + label_w + 1, 0), middle, label)
            return
        number_color = t.text_faint
        if row.kind in ("add", "del"):
            background, bar = (ADD_BG, ADD_BAR) if row.kind == "add" else (DEL_BG, DEL_BAR)
            painter.fillRect(0, top, width, LINE_H, QColor(background))
            painter.fillRect(0, top, BAR_W, LINE_H, QColor(bar))
            number_color = bar
        if row.number is not None:
            painter.setPen(QColor(number_color))
            painter.drawText(box.adjusted(BAR_W, 0, BAR_W + gutter - 8, 0),
                             middle | Qt.AlignmentFlag.AlignRight, str(row.number))
        text = painter.fontMetrics().elidedText(row.text, Qt.TextElideMode.ElideRight, width - text_x - 8)
        painter.setPen(QColor(t.text))
        painter.drawText(box.adjusted(text_x, 0, width - 8, 0), middle, text)


class DiffView(QScrollArea):
    """Unified diff pane. ``fileToggled(path, collapsed)`` fires on header clicks."""

    fileToggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None, theme: tokens.Theme = tokens.DARK) -> None:
        super().__init__(parent)
        self._theme = theme
        self._mono = _mono_font(theme)
        self._ui = QFont(self.font())
        self._ui.setPixelSize(theme.size_base)
        self._files: list[FileDiff] = []
        self._plain: list[str] = []
        self._notes: list[str] = []
        self._collapsed: set[int] = set()
        self._max_number = 0
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self._canvas = _Canvas(self)
        self.setWidget(self._canvas)

    def files(self) -> list[FileDiff]:
        return list(self._files)

    def clear(self) -> None:
        self.set_diff_text("")

    def set_payload(self, payload: dict[str, object] | None) -> None:
        """Show one ``workflow_diff`` payload (``source`` worktree/branch/stat)."""
        payload = payload or {}
        text, truncated = str(payload.get("text") or ""), bool(payload.get("truncated"))
        if payload.get("source") != "stat":
            self.set_diff_text(text, truncated)
            return
        note = "Full diff gone; last recorded change summary:"
        self._show([], text.splitlines(), [note if text else "No changes recorded yet."])

    def set_diff_text(self, text: str, truncated: bool = False) -> None:
        files = parse_unified_diff(text)
        plain = [] if files else text.splitlines()
        notes = []
        if truncated:
            notes.append(f"Diff truncated at {len(text.encode('utf-8')) // 1024} KB; the rest is not shown.")
        self._show(files, plain, notes)

    def set_collapsed(self, path: str, collapsed: bool) -> None:
        for index, diff in enumerate(self._files):
            if diff.path == path and (index in self._collapsed) != collapsed:
                self.toggle_file(index)

    def toggle_file(self, index: int) -> None:
        self._collapsed ^= {index}
        self._canvas.layout_items()
        self.fileToggled.emit(self._files[index].path, index in self._collapsed)

    def _show(self, files: list[FileDiff], plain: list[str], notes: list[str]) -> None:
        self._files, self._plain, self._notes, self._collapsed = files, plain, notes, set()
        numbers = [row.number or 0 for diff in files for row in diff.rows if row.kind != "gap"]
        self._max_number = max(numbers, default=0)
        self._canvas.layout_items()

    def _entries(self):
        if not self._files:
            yield from (("row", -1, DiffRow("note", note)) for note in self._notes)
            yield from (("row", -1, DiffRow("ctx", line)) for line in self._plain)
            return
        for index, diff in enumerate(self._files):
            yield "header", index, None
            if index not in self._collapsed:
                yield from (("row", index, row) for row in diff.rows)
        yield from (("row", -1, DiffRow("note", note)) for note in self._notes)
