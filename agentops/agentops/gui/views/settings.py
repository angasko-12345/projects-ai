"""Settings surface: repository selection, desktop behavior, shortcuts, about."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..settings import AppSettings
from ..widgets import Card, KeyValueGrid, ghost_button
from .base import BaseView

_SHORTCUTS = (
    ("Ctrl+K", "Open the command palette"),
    ("Ctrl+N", "Start a new task"),
    ("Ctrl+R", "Refresh the current view"),
    ("Ctrl+B", "Collapse or expand the sidebar"),
    ("Ctrl+1 … Ctrl+0", "Jump between sidebar views"),
)


class SettingsView(BaseView):
    """Client-side preferences; no controller state is modified here."""

    view_id = "settings"
    title = "Settings"

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        root = QVBoxLayout(host)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # Repository -----------------------------------------------------
        repo_card = Card("Repository")
        body = repo_card.body()
        row = QHBoxLayout()
        row.setSpacing(8)
        self._repo_edit = QLineEdit()
        self._repo_edit.setPlaceholderText("Path to the repository AgentOps drives")
        self._repo_edit.returnPressed.connect(self._apply_repository)
        row.addWidget(self._repo_edit, stretch=1)
        browse = ghost_button("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        apply_button = ghost_button("Apply")
        apply_button.clicked.connect(self._apply_repository)
        row.addWidget(apply_button)
        body.addLayout(row)
        self._recent = QListWidget()
        self._recent.setMaximumHeight(150)
        self._recent.itemActivated.connect(self._pick_recent)
        body.addWidget(self._recent)
        clear = ghost_button("Clear recent repositories")
        clear.clicked.connect(self._clear_recent)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(clear)
        body.addLayout(row2)
        root.addWidget(repo_card)

        # Desktop --------------------------------------------------------
        desktop_card = Card("Desktop")
        self._sidebar = QCheckBox("Collapse sidebar on startup")
        self._sidebar.toggled.connect(
            lambda checked: self.ctx.update_settings({"sidebar_collapsed": checked})
        )
        desktop_card.add(self._sidebar)
        desktop_note = QLabel(
            "Window geometry, sidebar state, and the active view are remembered "
            "automatically."
        )
        desktop_note.setProperty("role", "muted")
        desktop_note.setWordWrap(True)
        desktop_card.add(desktop_note)
        root.addWidget(desktop_card)

        # Shortcuts ------------------------------------------------------
        keys_card = Card("Keyboard shortcuts")
        keys_grid = KeyValueGrid(columns=2)
        for shortcut, description in _SHORTCUTS:
            keys_grid.add_row(description, shortcut, mono_value=True)
        keys_card.add(keys_grid)
        root.addWidget(keys_card)

        # About ----------------------------------------------------------
        about_card = Card("About")
        about_grid = KeyValueGrid(columns=2)
        about_grid.add_row("Product", "AgentOps")
        about_grid.add_row("Version", _version())
        about_grid.add_row(
            "Scope",
            "Local-first orchestrator for installed coding-agent CLIs.",
        )
        about_grid.add_row(
            "Theme",
            "Dark developer theme; tokens live in gui/tokens.py.",
        )
        about_card.add(about_grid)
        root.addWidget(about_card)

        root.addStretch(1)
        area.setWidget(host)
        outer.addWidget(area, stretch=1)
        self._sync()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._sync()

    def _sync(self) -> None:
        settings: AppSettings = self.ctx.settings()
        if not self._repo_edit.text():
            self._repo_edit.setPlaceholderText(
                settings.repository or "Path to the repository AgentOps drives"
            )
        self._recent.clear()
        for path in settings.recent_repositories:
            self._recent.addItem(QListWidgetItem(path))
        was_blocked = self._sidebar.blockSignals(True)
        self._sidebar.setChecked(bool(settings.sidebar_collapsed))
        self._sidebar.blockSignals(was_blocked)

    # ------------------------------------------------------------------
    def _browse(self) -> None:
        start = self._repo_edit.text() or self.ctx.settings().repository or ""
        chosen = QFileDialog.getExistingDirectory(self, "Choose repository", start)
        if chosen:
            self._repo_edit.setText(chosen)
            self._apply_repository()

    def _apply_repository(self) -> None:
        text = self._repo_edit.text().strip()
        if not text:
            return
        self.ctx.update_settings({"repository": text})
        self.ctx.toast("Repository updated", text, "success")
        self._repo_edit.clear()
        self._sync()

    def _pick_recent(self, item: QListWidgetItem) -> None:
        path = item.text().strip()
        if not path:
            return
        self.ctx.update_settings({"repository": path})
        self.ctx.toast("Repository updated", path, "success")
        self._sync()

    def _clear_recent(self) -> None:
        self.ctx.update_settings({"recent_repositories": []})
        self._sync()


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("agentops")
    except Exception:
        return "development build"
