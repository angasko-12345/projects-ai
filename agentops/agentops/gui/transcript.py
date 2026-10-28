"""Run transcript: one workflow read top to bottom in the middle pane.

Widgets only: every string comes from ``control_center.build_transcript`` (and
its ``check_text``/``failure_text``), which read controller payload fields.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import control_center as projection
from .components import Chip, IconButton, Pill, StatusGlyph
from .icons import icon
from .tokens import DARK, qcolor, qfont, with_alpha

COMPACT_BELOW = 1280  # window width: side padding 40 above it, 20 below
_BOLD, _MEDIUM = QFont.Weight.DemiBold, QFont.Weight.Medium
_T = DARK


class TextLabel(QLabel):
    """Transcript text at a fixed pixel size; ``elide`` keeps it to one line."""

    def __init__(self, text: str, px: int, color: str, *, weight=None, mono=False, elide=False):
        super().__init__(text)
        self._elide = elide
        font = qfont(_T, px, mono=mono)
        if weight is not None:
            font.setWeight(weight)
        self.setFont(font)
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setWordWrap(not elide)
        if elide:
            self.setToolTip(text)
            self.setMinimumWidth(24)
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._elide:
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), self.alignment(), text)


def _row(*items, spacing: int = 8) -> QHBoxLayout:
    """A row; an int item is a stretch, an eliding label takes the free width."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for item in items:
        if isinstance(item, int):
            layout.addStretch(item)
        else:
            layout.addWidget(item, int(bool(getattr(item, "_elide", False))))
    return layout


def _box(name: str, background: str, border: str, margins=(14, 10, 14, 10), spacing=6):
    frame = QFrame()
    frame.setObjectName(name)
    frame.setStyleSheet(
        f"QFrame#{name} {{ background: {background}; border: 1px solid {border}; "
        f"border-radius: {_T.radius_md}px; }}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def _mark(name: str, color: str, size: int = 14) -> QLabel:
    label = QLabel()
    label.setPixmap(icon(name, color, size))
    label.setFixedSize(size, size)
    return label


class TranscriptScroll(QScrollArea):
    """Scroll body whose bottom edge fades into the page (and the composer)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._fade = QWidget(self)
        self._fade.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fade.paintEvent = self._paint_fade

    def _paint_fade(self, _event) -> None:
        painter = QPainter(self._fade)
        gradient = QLinearGradient(0, 0, 0, self._fade.height())
        gradient.setColorAt(0, qcolor(_T.bg, 0.0))
        gradient.setColorAt(1, qcolor(_T.bg, 1.0))
        painter.fillRect(self._fade.rect(), gradient)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        port = self.viewport().geometry()
        self._fade.setGeometry(QRect(port.left(), port.bottom() - 35, port.width(), 36))
        self._fade.raise_()


class ReadinessChip(QFrame):
    """Header chip: "Ready to merge" or "Not ready · n gates open" (+ reasons)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("readinessChip")
        self._mark, self._label = QLabel(), QLabel()
        self._label.setFont(qfont(_T, _T.size_caption))
        layout = _row(self._mark, self._label, spacing=6)
        layout.setContentsMargins(8, 2, 9, 2)
        self.setLayout(layout)
        self.setVisible(False)

    def text(self) -> str:
        return self._label.text()

    def set_readiness(self, readiness: dict) -> None:
        color = _T.success if readiness.get("ready") else _T.warning
        self._mark.setPixmap(
            icon("git-merge" if readiness.get("ready") else "circle-pause", color, 13)
        )
        self._label.setText(str(readiness.get("chip") or ""))
        self._label.setStyleSheet(f"color: {color}; background: transparent;")
        self.setStyleSheet(
            f"QFrame#readinessChip {{ background: {with_alpha(color, 0.10)}; "
            f"border: 1px solid {with_alpha(color, 0.25)}; border-radius: {_T.radius_sm}px; }}"
        )
        self.setToolTip("\n".join(str(reason) for reason in readiness.get("reasons") or ()))
        self.setVisible(bool(self._label.text()))


def _checklist(verification: dict) -> QFrame:
    card = projection.check_text(verification)
    passed = card["status"] == "passed"
    frame, layout = _box("checklist", _T.surface, _T.border, (14, 11, 14, 12), 7)
    title = TextLabel(card["title"], _T.size_body, _T.text, weight=_BOLD)
    meta = TextLabel(card["meta"], _T.size_caption, _T.text_muted, elide=True)
    layout.addLayout(
        _row(_mark("shield-check", _T.success if passed else _T.danger, 15), title, meta)
    )
    for check in card["checks"]:
        ok = check["status"] == "passed"
        cells = [
            TextLabel(check["name"], _T.size_body, _T.text, elide=True),
            TextLabel(check["class"], _T.size_caption, _T.text_faint, elide=True),
            TextLabel(check["reason"], _T.size_caption, _T.danger, elide=True),
            TextLabel(check["duration"], _T.size_caption, _T.text_faint, elide=True),
        ]
        for cell, width in zip((cells[0], cells[1], cells[3]), (84, 96, 44), strict=True):
            cell.setFixedWidth(width)
        cells[3].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(
            _row(_mark("check" if ok else "x", _T.success if ok else _T.danger), *cells)
        )
    return frame


def _callout(failure: dict) -> QFrame:
    text = projection.failure_text(failure)
    frame, layout = _box("failureCallout", with_alpha(_T.danger, 0.07), with_alpha(_T.danger, 0.22))
    head: list = [
        _mark("triangle-alert", _T.danger),
        TextLabel(text["title"], _T.size_body, _T.text, weight=_BOLD),
    ]
    head += [Pill(text["severity"])] if text["severity"] else []
    head.append(1)
    if text["action"]:
        head.append(TextLabel("Recommended:", _T.size_caption, _T.text_faint))
        head.append(TextLabel(text["action"], _T.size_caption, _T.text, weight=_MEDIUM))
    layout.addLayout(_row(*head))
    if text["error"]:
        layout.addWidget(
            TextLabel(text["error"], _T.size_caption, _T.text_muted, mono=True, elide=True)
        )
    return frame


def _attempt(attempt: dict, open_diff) -> QWidget:
    text, live = attempt["text"], bool(attempt["live"])
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addLayout(
        _row(
            StatusGlyph("running" if live else attempt["status"], 14),
            TextLabel(text["title"], _T.size_body, _T.text, weight=_MEDIUM),
            TextLabel(text["meta"], _T.size_caption, _T.text_muted, elide=True),
            TextLabel(text["duration"], _T.size_caption, _T.accent if live else _T.text_muted),
        )
    )
    if attempt["error"] and not (attempt["verifications"] or attempt["failures"]):
        layout.addWidget(
            TextLabel(attempt["error"], _T.size_caption, _T.danger, mono=True, elide=True)
        )
    for verification in attempt["verifications"]:
        layout.addWidget(_checklist(verification))
    for failure in attempt["failures"]:
        layout.addWidget(_callout(failure))
    if text["files"]:
        frame, _layout = _box("filesChip", _T.surface, _T.border, (12, 6, 8, 6))
        stat = [
            TextLabel(text[key], _T.size_body, color, mono=True)
            for key, color in (("insertions", _T.success), ("deletions", _T.danger))
            if text[key]
        ]
        button = QPushButton("Open diff")
        button.setProperty("variant", "ghost")
        button.clicked.connect(lambda: open_diff(attempt["id"]))
        files = TextLabel(text["files"], _T.size_body, _T.text, weight=_MEDIUM)
        _layout.addLayout(_row(_mark("file-diff", _T.text_faint, 13), files, *stat, 1, button))
        layout.addWidget(frame)
    return widget


def _stage(block: dict, open_diff) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    queued = block["state"] in ("pending", "blocked") and not block["attempts"]
    label = TextLabel(
        block["label"], _T.size_title, _T.text_faint if queued else _T.text, weight=_BOLD
    )
    label.setWordWrap(False)
    head: list = [
        StatusGlyph(block["state"], 16),
        label,
        TextLabel(block["description"], _T.size_body, _T.text_muted, elide=True),
    ]
    head += [Chip(block["agent"], "bot")] if block["agent"] else []
    layout.addLayout(_row(*head, spacing=10))
    inner = QVBoxLayout()
    inner.setContentsMargins(26, 0, 0, 0)
    inner.setSpacing(12)
    if block["result"]:
        inner.addWidget(TextLabel(block["result"], _T.size_prose, _T.text))
    for attempt in block["attempts"]:
        inner.addWidget(_attempt(attempt, open_diff))
    layout.addLayout(inner)
    return widget


class Transcript(QWidget):
    """Prompt bubble, "Worked for" summary, one block per stage, Finalize.

    ``set_workflow(...)`` takes ``control_center.build_transcript``'s arguments
    and returns the model it rendered; ``clear()`` empties it.
    ``add_summary_widget(w)`` adds to the summary the "Worked for" chevron
    expands. ``open_diff_requested(run_id)`` fires from a changed-files chip.
    """

    open_diff_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model: dict = {}
        self._side = 0
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(18)
        self._bubble, bubble = _box("promptBubble", _T.surface_alt, _T.border, spacing=4)
        self._bubble.setMaximumWidth(560)
        self._prompt = TextLabel("", _T.size_prose, _T.text)
        self._prompt_meta = TextLabel("", _T.size_caption, _T.text_faint)
        bubble.addWidget(self._prompt)
        bubble.addWidget(self._prompt_meta)
        bubble_row = _row(1, self._bubble)
        bubble_row.setStretch(1, 4)  # widen to the 560px cap before wrapping
        self._layout.addLayout(bubble_row)
        self._worked = TextLabel("", _T.size_body, _T.text_muted)
        self._worked_meta = TextLabel("", _T.size_body, _T.text_faint)
        self._toggle = IconButton("chevron-right", "Show summary", size=22, icon_size=13)
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._show_summary)
        self._layout.addLayout(_row(self._worked, self._worked_meta, self._toggle, 1, spacing=6))
        self._summary = QWidget()
        QVBoxLayout(self._summary).setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._summary)
        self._blocks = QWidget()
        self._layout.addWidget(self._blocks)
        self._finalize_glyph = StatusGlyph("pending", 16)
        self._finalize = TextLabel("Finalize", _T.size_title, _T.text, weight=_BOLD)
        self._finalize_text = TextLabel("", _T.size_body, _T.text_muted)
        for label in (self._worked, self._worked_meta, self._finalize):
            label.setWordWrap(False)
        finalize = (self._finalize_glyph, self._finalize, self._finalize_text)
        self._layout.addLayout(_row(*finalize, 1, spacing=10))
        self._reasons = TextLabel("", _T.size_caption, _T.text_faint)
        self._reasons.setContentsMargins(26, 0, 0, 0)
        self._layout.addWidget(self._reasons)
        self._layout.addStretch(1)
        self._header = (self._bubble, self._worked, self._worked_meta, self._toggle, *finalize)
        self._apply_padding()
        self.clear()

    def set_workflow(self, *args, **kwargs) -> dict:
        model = projection.build_transcript(*args, **kwargs)
        self._render(model)
        return model

    def clear(self) -> None:
        self._render({})

    def model(self) -> dict:
        return self._model

    def add_summary_widget(self, widget: QWidget) -> None:
        self._summary.layout().addWidget(widget)

    def _render(self, model: dict) -> None:
        self._model = model
        self._prompt.setText(str(model.get("prompt") or ""))
        self._prompt_meta.setText(str(model.get("prompt_meta") or ""))
        self._prompt_meta.setVisible(bool(self._prompt_meta.text()))
        self._worked.setText(str(model.get("worked") or ""))
        self._worked_meta.setText(f"· {model['worked_meta']}" if model else "")
        # A fresh host swapped in shown: a poll refresh never paints a frame
        # with the stage blocks missing.
        host = QWidget()
        blocks = QVBoxLayout(host)
        blocks.setContentsMargins(0, 0, 0, 0)
        blocks.setSpacing(22)
        for block in model.get("blocks") or ():
            blocks.addWidget(_stage(block, self.open_diff_requested.emit))
        self._layout.replaceWidget(self._blocks, host)
        host.setVisible(bool(blocks.count()))
        self._blocks.hide()
        self._blocks.deleteLater()
        self._blocks = host
        finalize = model.get("finalize") or {}
        self._finalize_glyph.set_status(finalize.get("state") or "pending")
        self._finalize_text.setText(str(finalize.get("text") or ""))
        self._reasons.setText("\n".join(str(r) for r in finalize.get("reasons") or ()))
        self._reasons.setVisible(bool(self._reasons.text()))
        for widget in self._header:
            widget.setVisible(bool(model))
        self._summary.setVisible(bool(model) and self._toggle.isChecked())

    def _show_summary(self, on: bool) -> None:
        self._toggle.set_icon("chevron-down" if on else "chevron-right")
        self._toggle.setToolTip("Hide summary" if on else "Show summary")
        self._summary.setVisible(on and bool(self._model))

    def _apply_padding(self) -> None:
        window = self.window()
        side = 20 if window is not self and window.width() < COMPACT_BELOW else 40
        if side != self._side:
            self._side = side
            self._layout.setContentsMargins(side, 20, side, 28)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_padding()
