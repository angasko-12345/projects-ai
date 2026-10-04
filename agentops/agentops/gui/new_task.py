"""New Task dialog: repository, description, agent strategy, verification.

All option loading (agent profiles, verification profiles) goes through the
bridge; the dialog never touches the controller synchronously. The shell
reads the collected values via :meth:`values` after a successful ``exec``.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .views.base import AsyncMixin, ViewContext
from .widgets import divider, faint, ghost_button, label, muted

_WARNING = "A description and repository are required to start a task."


class NewTaskDialog(QDialog, AsyncMixin):
    """First-class task creation flow delegating to ``AgentOpsController``."""

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)
        self.setWindowTitle("New Task")
        self.setModal(True)
        self.setMinimumSize(580, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(label("New Task", "title"))
        layout.addWidget(muted(
            "AgentOps plans, implements, verifies, and reviews the change in an "
            "isolated worktree."
        ))

        # -- repository ------------------------------------------------
        layout.addWidget(label("Repository", "subtitle"))
        repo_row = QHBoxLayout()
        self._repository = QComboBox()
        self._repository.setEditable(True)
        self._repository.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        settings = context.settings()
        paths = list(dict.fromkeys(
            [settings.repository or "", *settings.recent_repositories]
        ))
        for path in paths:
            if path:
                self._repository.addItem(path)
        if settings.repository:
            self._repository.setCurrentText(settings.repository)
        repo_row.addWidget(self._repository, stretch=1)
        browse = ghost_button("Browse...")
        browse.clicked.connect(self._browse)
        repo_row.addWidget(browse)
        layout.addLayout(repo_row)

        # -- description ----------------------------------------------
        layout.addWidget(label("What should the agents do?", "subtitle"))
        self._description = QPlainTextEdit()
        self._description.setPlaceholderText(
            "Describe the outcome, e.g. \"Fix the failing widget tests and update "
            "the README to match.\""
        )
        self._description.setTabChangesFocus(True)
        self._description.setMinimumHeight(140)
        layout.addWidget(self._description)

        layout.addWidget(divider())

        # -- agent strategy --------------------------------------------
        layout.addWidget(label("Agent strategy", "subtitle"))
        self._auto = QRadioButton("Automatic routing (router picks per task role)")
        self._auto.setChecked(True)
        self._explicit = QRadioButton("Explicit agent (pin one agent to every role)")
        strategy_row = QHBoxLayout()
        strategy_column = QVBoxLayout()
        strategy_column.addWidget(self._auto)
        strategy_column.addWidget(self._explicit)
        strategy_row.addLayout(strategy_column)
        self._agent_combo = QComboBox()
        self._agent_combo.setEnabled(False)
        self._agent_combo.setMinimumWidth(240)
        strategy_row.addWidget(self._agent_combo, alignment=Qt.AlignmentFlag.AlignTop)
        strategy_row.addStretch(1)
        layout.addLayout(strategy_row)
        self._auto.toggled.connect(self._sync_strategy)
        self._explicit.toggled.connect(self._sync_strategy)

        # -- verification ----------------------------------------------
        layout.addWidget(label("Verification profile", "subtitle"))
        verification_row = QHBoxLayout()
        self._verification = QComboBox()
        verification_row.addWidget(self._verification, stretch=1)
        self._verification_hint = faint("")
        verification_row.addWidget(self._verification_hint)
        layout.addLayout(verification_row)

        layout.addStretch(1)

        self._warning = QLabel(_WARNING)
        self._warning.setProperty("role", "subtitle")
        self._warning.setWordWrap(True)
        layout.addWidget(self._warning)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._start = QPushButton("Start Task")
        self._start.setProperty("variant", "primary")
        self._start.setEnabled(False)
        self._start.clicked.connect(self.accept)
        buttons.addWidget(self._start)
        layout.addLayout(buttons)

        self._description.textChanged.connect(self._sync_start)
        self._repository.currentTextChanged.connect(self._sync_start)
        self._sync_start()

    # -- option loading ----------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        controller = self.ctx.controller
        self.submit(
            "task-options",
            lambda: controller.task_options(),
            self._on_options,
        )
        self.submit(
            "agents",
            lambda: controller.list_agent_profiles(),
            self._on_agents,
        )

    def _on_options(self, options: object) -> None:
        if not isinstance(options, dict):
            return
        default = str(options.get("default_verification_profile") or "")
        profiles = [str(item) for item in options.get("verification_profiles") or ()]
        self._verification.clear()
        self._verification.addItem("Default (from config)", None)
        for profile in profiles:
            self._verification.addItem(profile, profile)
        if default and default in profiles:
            self._verification.setCurrentText(default)
        self._verification_hint.setText(
            "Auto routing is enabled." if options.get("routing_enabled")
            else "Router disabled; the legacy selector will choose agents."
        )

    def _on_agents(self, profiles: object) -> None:
        if not isinstance(profiles, list):
            return
        self._agent_combo.clear()
        for profile in profiles:
            identifier = str(profile.get("identifier") or "")
            display = str(profile.get("display_name") or identifier)
            text = f"{display} ({identifier})"
            if not profile.get("availability"):
                text += " - unavailable"
            self._agent_combo.addItem(text, identifier)

    # -- interaction ---------------------------------------------------
    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose repository", self._repository.currentText() or ""
        )
        if chosen:
            self._repository.setCurrentText(chosen)

    def _sync_strategy(self, *_args: object) -> None:
        self._agent_combo.setEnabled(self._explicit.isChecked())

    def _sync_start(self, *_args: object) -> None:
        has_description = bool(self._description.toPlainText().strip())
        has_repository = bool(self._repository.currentText().strip())
        valid = has_description and has_repository
        self._start.setEnabled(valid)
        self._warning.setVisible(not valid)

    # -- result --------------------------------------------------------
    def values(self) -> dict[str, object]:
        """Collected dialog state for ``shell.start_task``."""
        agent = None
        if self._explicit.isChecked():
            agent = self._agent_combo.currentData()
        profile = self._verification.currentData()
        return {
            "description": self._description.toPlainText().strip(),
            "repository": self._repository.currentText().strip() or None,
            "agent": str(agent) if agent else None,
            "verification_profile": str(profile) if profile else None,
        }
