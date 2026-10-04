"""Command palette (Ctrl+K).

The shell supplies the command list on every open (navigation, primary
actions, recent repositories). Matching is a small deterministic scorer -
prefix and word-boundary hits outrank substring hits, subsequence matches
last. No fuzzy libraries, no state beyond the visible filtered list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .tokens import DARK
from .widgets import CommandList


@dataclass
class Command:
    title: str
    run: Callable[[], None]
    keywords: str = ""
    shortcut: str = ""
    group: str = ""


def _subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(character in iterator for character in needle)


def score_command(query: str, command: Command) -> float | None:
    """Deterministic match score; ``None`` means no match."""
    normalized = query.strip().lower()
    if not normalized:
        return 0.0
    title = command.title.lower()
    keywords = command.keywords.lower()
    index = title.find(normalized)
    if index >= 0:
        score = 300.0 - index
        if index == 0:
            score += 100.0
        elif title[index - 1] in " /-:>(":
            score += 50.0
        return score
    if _subsequence(normalized, title):
        return 150.0
    if normalized in keywords:
        return 100.0
    tokens = normalized.split()
    if tokens and all(token in title or token in keywords for token in tokens):
        return 80.0
    return None


class _PaletteInput(QLineEdit):
    """Input that hands navigation keys to the palette instead of itself."""

    def __init__(self, palette: "CommandPalette"):
        super().__init__(palette)
        self._palette = palette

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._palette.handle_key(event.key())
            return
        super().keyPressEvent(event)


class CommandPalette(QDialog):
    """Frameless command list; emits ``command_chosen`` and closes."""

    command_chosen = Signal(object)  # Command

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._commands: list[Command] = []
        self._visible: list[Command] = []

        frame = QFrame()
        frame.setObjectName("Palette")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(DARK.space_md, DARK.space_sm,
                                  DARK.space_md, DARK.space_sm)
        layout.setSpacing(DARK.space_sm)

        header = QHBoxLayout()
        header.setSpacing(DARK.space_sm)
        prompt = QLabel(">")
        prompt.setProperty("role", "title")
        header.addWidget(prompt)
        self.input = _PaletteInput(self)
        self.input.setPlaceholderText("Type a command or view name...")
        self.input.setProperty("role", "search")
        self.input.setClearButtonEnabled(False)
        header.addWidget(self.input, stretch=1)
        hint = QLabel("Esc to close")
        hint.setProperty("role", "faint")
        header.addWidget(hint)
        layout.addLayout(header)

        self.commands_list = CommandList()
        self.commands_list.setMinimumHeight(220)
        self.commands_list.setMaximumHeight(320)
        self.commands_list.chosen.connect(self._on_chosen)
        layout.addWidget(self.commands_list)

        self.input.textChanged.connect(self._refilter)
        self.input.installEventFilter(self)

    # -- data ---------------------------------------------------------
    def set_commands(self, commands: Sequence[Command]) -> None:
        self._commands = list(commands)
        self._refilter(self.input.text())

    def visible_commands(self) -> list[Command]:
        return list(self._visible)

    # -- filtering ----------------------------------------------------
    def _refilter(self, text: str = "") -> None:
        scored: list[tuple[float, str, Command]] = []
        for command in self._commands:
            score = score_command(text, command)
            if score is None:
                continue
            scored.append((score, command.title.lower(), command))
        scored.sort(key=lambda item: (-item[0], item[1]))
        self._visible = [item[2] for item in scored]
        self.commands_list.set_commands([
            (command.title + (f"    {command.shortcut}" if command.shortcut else ""),
             command.shortcut)
            for command in self._visible
        ])

    # -- interaction --------------------------------------------------
    def handle_key(self, key: int) -> None:
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.commands_list.keyPressEvent(
                QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            )
            return
        self.run_current()

    def run_current(self) -> None:
        index = self.commands_list.current_index()
        if 0 <= index < len(self._visible):
            command = self._visible[index]
            self.close()
            self.command_chosen.emit(command)
            command.run()

    def _on_chosen(self, index: int) -> None:
        if 0 <= index < len(self._visible):
            command = self._visible[index]
            self.close()
            self.command_chosen.emit(command)
            command.run()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.handle_key(event.key())
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)

    # -- presentation -------------------------------------------------
    def open_centered(self) -> None:
        """Show over the parent window, roughly a command-palette offset."""
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            x = parent.x() + max(24, (parent.width() - self.width()) // 2)
            y = parent.y() + 120
            self.move(x, y)
        else:
            self.move(160, 120)
        self.show()
        self.raise_()
        self.input.setFocus()
        self.input.selectAll()
