"""Composer: the new-task card pinned under the run transcript.

It starts work through the shell's existing ``start_task`` path (the same one
the New Task dialog uses). Agent and verification choices come from the
controller's ``list_agent_profiles`` and ``task_options`` reads; the footer
chips show the selected workflow's ``worktree_ref`` fields.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .components import Chip
from .context import AsyncMixin, ViewContext
from .icons import qicon
from .tokens import DARK, qfont, with_alpha
from .transcript import COMPACT_BELOW
from .widgets import agent_icon

_T = DARK
_EMPTY = "Describe the task first."


class Composer(QWidget, AsyncMixin):
    """Card + footer chips; ``ctx.start_task`` does the actual launch."""

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)
        self._agents_loaded = False
        self._options_loaded = False
        self._side = -1

        self._outer = QVBoxLayout(self)
        self._outer.setSpacing(_T.space_sm)
        self._card = QFrame()
        self._card.setObjectName("composer")
        self._card.setStyleSheet(
            f"QFrame#composer {{ background: {_T.surface}; border: 1px solid "
            f"{_T.border_strong}; border-radius: {_T.radius_lg}px; }}"
        )
        card = QVBoxLayout(self._card)
        card.setContentsMargins(_T.space_xs, _T.space_xs, _T.space_md, _T.space_md)
        card.setSpacing(_T.space_xs)

        self._input = QPlainTextEdit()
        self._input.setFont(qfont(_T, _T.size_prose))
        self._input.setStyleSheet("QPlainTextEdit { background: transparent; border: none; }")
        self._input.setTabChangesFocus(True)
        self._input.setFixedHeight(56)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._sync_send)
        card.addWidget(self._input)

        self._warning = QLabel(_EMPTY)
        self._warning.setStyleSheet(f"color: {_T.danger}; padding-left: {_T.space_md}px;")
        self._warning.hide()
        card.addWidget(self._warning)

        row = QHBoxLayout()
        row.setContentsMargins(_T.space_sm, 0, 0, 0)
        row.setSpacing(_T.space_sm)
        self._agent = self._picker("Which agent should do this task")
        self._agent.addItem(qicon("bot", _T.text_muted, 14), "Auto routing", None)
        self._verification = self._picker("Verification profile for this task")
        row.addWidget(self._agent)
        row.addWidget(self._verification)
        row.addStretch(1)
        self._send = QToolButton()
        self._send.setObjectName("composerSend")
        self._send.setIcon(qicon("arrow-up", _T.on_accent, 16))
        self._send.setFixedSize(32, 32)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setToolTip("Start task  (Ctrl+Enter)")
        self._send.setAccessibleName("Start task")
        self._send.setStyleSheet(
            f"QToolButton#composerSend {{ background: {_T.accent_strong}; border: none; "
            f"border-radius: 16px; }} QToolButton#composerSend:hover {{ background: "
            f"{_T.accent}; }} QToolButton#composerSend:disabled {{ background: "
            f"{with_alpha(_T.accent_strong, 0.4)}; }}"
        )
        self._send.clicked.connect(self._submit)
        row.addWidget(self._send)
        card.addLayout(row)
        self._outer.addWidget(self._card)

        footer = QHBoxLayout()
        footer.setContentsMargins(_T.space_sm, 0, 0, 0)
        footer.setSpacing(_T.space_md)
        self._worktree_chip = Chip("Worktree", "folder")
        self._branch_chip = Chip("", "git-branch")
        self._base_chip = Chip("", "git-merge")
        for chip in (self._worktree_chip, self._branch_chip, self._base_chip):
            chip.setStyleSheet("QFrame { background: transparent; border: none; }")
            chip.hide()
            footer.addWidget(chip)
        footer.addStretch(1)
        self._outer.addLayout(footer)
        self._ref: dict = {}
        self.set_repository("")
        self._sync_send()

    @staticmethod
    def _picker(tooltip: str) -> QComboBox:
        combo = QComboBox()
        combo.setProperty("scope", True)  # flat chip, like the sidebar scope combo
        combo.setToolTip(tooltip)
        combo.setAccessibleName(tooltip)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        return combo

    # -- data ------------------------------------------------------------
    def set_repository(self, repository: str) -> None:
        name = Path(str(repository or "")).name or "this repository"
        self._input.setPlaceholderText(f"Describe a new task for {name}…  (Ctrl+Enter to start)")

    def set_worktree(self, ref: object) -> None:
        """Footer chips from the selected workflow's ``worktree_ref``."""
        self._ref = dict(ref) if isinstance(ref, dict) else {}
        path = str(self._ref.get("path") or "")
        branch = str(self._ref.get("branch") or "")
        base = str(self._ref.get("base_branch") or "")
        self._worktree_chip.setToolTip(path)
        self._branch_chip.set_text(branch)
        self._base_chip.set_text(f"base {base}" if base else "")
        self._apply_width()

    def load_options(self) -> None:
        controller = self.ctx.controller
        if not self._agents_loaded:
            self.submit("agents", lambda: controller.list_agent_profiles(), self._on_agents)
        if not self._options_loaded:
            self.submit("task-options", lambda: controller.task_options(), self._on_options)

    def _on_agents(self, profiles: object) -> None:
        if not isinstance(profiles, list):
            return
        current = self._agent.currentData()
        while self._agent.count() > 1:
            self._agent.removeItem(1)
        for profile in profiles:
            if not isinstance(profile, dict) or not profile.get("availability"):
                continue
            identifier = str(profile.get("identifier") or "")
            display = str(profile.get("display_name") or identifier)
            self._agent.addItem(agent_icon(identifier, display), display, identifier)
        index = self._agent.findData(current)
        self._agent.setCurrentIndex(index if index >= 0 else 0)
        self._agents_loaded = True

    def _on_options(self, options: object) -> None:
        if not isinstance(options, dict):
            return
        default = str(options.get("default_verification_profile") or "")
        profiles = [str(item) for item in options.get("verification_profiles") or ()]
        self._verification.clear()
        shield = qicon("shield-check", _T.text_muted, 14)
        for profile in profiles:
            self._verification.addItem(shield, f"Verify: {profile}", profile)
        if default in profiles:
            self._verification.setCurrentIndex(profiles.index(default))
        self._verification.setVisible(bool(profiles))
        self._options_loaded = True

    # -- interaction -------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if (
            watched is self._input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._submit()
            return True
        return super().eventFilter(watched, event)

    def _sync_send(self) -> None:
        has_text = bool(self._input.toPlainText().strip())
        self._send.setEnabled(has_text)
        if has_text:
            self._warning.hide()

    def _submit(self) -> None:
        """Hand the text to ``start_task``; empty input is rejected inline."""
        text = self._input.toPlainText().strip()
        if not text:
            self._warning.show()
            self._input.setFocus()
            return
        start = self.ctx.start_task
        if start is None:
            self.ctx.toast("Unavailable", "Task creation is not wired in this context.", "error")
            return
        agent = self._agent.currentData()
        profile = self._verification.currentData()
        started = start(
            text,
            self.ctx.repository() or None,
            str(agent) if agent else None,
            str(profile) if profile else None,
        )
        if started is not False:
            self._input.clear()

    # -- layout ------------------------------------------------------------
    def _apply_width(self) -> None:
        """Side padding follows the transcript's; narrow windows show the branch only."""
        window = self.window()
        compact = window is not self and window.width() < COMPACT_BELOW
        side = 12 if compact else 32
        if side != self._side:
            self._side = side
            self._outer.setContentsMargins(side, 0, side, _T.space_sm)
        branch = bool(self._ref.get("branch"))
        self._branch_chip.setVisible(branch)
        self._worktree_chip.setVisible(bool(self._ref.get("path")) and not compact)
        self._base_chip.setVisible(bool(self._ref.get("base_branch")) and not compact)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._apply_width()
        self.load_options()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_width()
