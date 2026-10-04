"""Application shell: window chrome, navigation, status, shortcuts, tray.

Owns the controller bridge, repository selection, New Task flow, operation
lifecycle (status/polling/follow-the-new-workflow), settings persistence, and
graceful shutdown. Views live in ``views`` and receive everything through
:class:`ViewContext`.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .bridge import ControllerBridge
from .format import format_duration
from .new_task import NewTaskDialog
from .palette import Command, CommandPalette
from .settings import AppSettings, load_settings, save_settings
from .toast import ToastHost
from .tokens import DARK, status_colors
from .views import VIEW_SPECS, create_views
from .views.base import AsyncMixin, ViewContext
from .widgets import PulsingDot, faint, ghost_button, label

_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+K", "open_palette"),
    ("Ctrl+N", "open_new_task"),
    ("Ctrl+R", "refresh_active"),
    ("Ctrl+B", "toggle_sidebar"),
)

# Sidebar grouping. Every view id in VIEW_SPECS must appear here exactly once
# (enforced by tests/test_gui_qt.py); the first id of each group renders a
# section label, and the order must follow VIEW_SPECS.
_NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Orchestrate", ("dashboard", "tasks", "workflows", "agents")),
    ("Inspect", ("runs", "verification", "failures", "worktrees", "artifacts")),
    ("Configure", ("settings",)),
)


def _app_icon() -> QIcon:
    """Programmatic app icon: rounded accent tile with an 'A' mark."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(DARK.accent))
    painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setPixelSize(34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "A")
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow, AsyncMixin):
    """Persistent shell hosting the sidebar, top bar, and stacked views."""

    def __init__(self, controller: object, settings: AppSettings | None = None,
                 settings_file: Path | None = None):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("AgentOps")
        self.setMinimumSize(1024, 680)
        self._controller = controller
        self._settings = settings if settings is not None else load_settings()
        self._settings_file = settings_file
        self._repository = (self._settings.repository or "").strip()
        self._bridge = ControllerBridge(controller, self)
        self._init_async(self._bridge)
        self._bridge.operation_event.connect(self._on_operation_event)

        self._active = False
        self._operation_started = 0.0
        self._pre_workflow_id: str | None = None
        self._follow_enabled = False
        self._first_show = True

        self._views: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._palette = CommandPalette(self)

        self._build_chrome()
        self._build_views()
        self._build_shortcuts()
        self._build_tray()

        self._toasts = ToastHost(self)
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._on_poll)

        self._apply_sidebar()
        self._sync_repository_label()
        self._set_status_idle()

    # ------------------------------------------------------------------
    # chrome
    # ------------------------------------------------------------------
    def _build_chrome(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- top bar ---------------------------------------------------
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        bar = QHBoxLayout(topbar)
        bar.setContentsMargins(18, DARK.space_sm, 18, DARK.space_sm)
        bar.setSpacing(DARK.space_sm)

        # Lives in the top bar, not the sidebar, so collapsing the sidebar
        # never traps the navigation (Ctrl+B toggles the same state).
        self._sidebar_toggle = ghost_button("Hide sidebar")
        self._sidebar_toggle.clicked.connect(self.toggle_sidebar)
        bar.addWidget(self._sidebar_toggle)

        repo_column = QVBoxLayout()
        repo_column.setSpacing(0)
        repo_column.addWidget(faint("Current repository"))
        self._repo_label = QLabel("")
        self._repo_label.setProperty("role", "mono")
        self._repo_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        repo_column.addWidget(self._repo_label)
        bar.addLayout(repo_column)
        change = ghost_button("Change...")
        change.clicked.connect(self.choose_repository)
        bar.addWidget(change)

        bar.addStretch(1)

        status_row = QHBoxLayout()
        status_row.setSpacing(DARK.space_sm)
        self._status_dot = PulsingDot(size=9)
        status_row.addWidget(self._status_dot)
        self._status_label = QLabel("Idle")
        self._status_label.setProperty("role", "muted")
        status_row.addWidget(self._status_label)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("variant", "danger")
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self.cancel_operation)
        status_row.addWidget(self._cancel_btn)
        bar.addLayout(status_row)

        self._new_task_btn = QPushButton("New Task")
        self._new_task_btn.setProperty("variant", "primary")
        self._new_task_btn.clicked.connect(self.open_new_task)
        bar.addWidget(self._new_task_btn)
        root.addWidget(topbar)

        # -- body ------------------------------------------------------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._sidebar = QWidget()
        self._sidebar.setObjectName("Sidebar")
        self._sidebar.setFixedWidth(216)
        side = QVBoxLayout(self._sidebar)
        side.setContentsMargins(DARK.space_sm, DARK.space_md,
                                DARK.space_sm, DARK.space_sm)
        side.setSpacing(2)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(DARK.space_sm)
        brand_row.setContentsMargins(DARK.space_xs, 0, 0, 0)
        mark = QLabel()
        mark.setPixmap(_app_icon().pixmap(16, 16))
        brand_row.addWidget(mark)
        brand = QLabel("AgentOps")
        brand.setProperty("role", "title")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        side.addLayout(brand_row)

        self._nav_layout = QVBoxLayout()
        self._nav_layout.setSpacing(2)
        side.addLayout(self._nav_layout)
        side.addStretch(1)
        side.addWidget(faint("Ctrl+K  command palette"))
        body.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        body.addWidget(self._stack, stretch=1)
        root.addLayout(body, stretch=1)
        self.setCentralWidget(central)

    def _build_views(self) -> None:
        context = ViewContext(
            bridge=self._bridge,
            repository=lambda: self._repository,
            navigate=self.navigate,
            toast=self.toast,
            settings=lambda: self._settings,
            open_task=self.open_task,
            open_run=self.open_run,
            operation_active=lambda: self._active,
            update_settings=self.update_settings,
        )
        self._views_ctx = context
        self._views = create_views(context)
        group_starters = {ids[0]: name for name, ids in _NAV_GROUPS}
        for index, (view_id, title, _factory) in enumerate(VIEW_SPECS):
            view = self._views.get(view_id)
            if view is None:
                continue
            group_name = group_starters.get(view_id)
            if group_name is not None:
                group_label = label(group_name, "group")
                group_label.setContentsMargins(0, DARK.space_md, 0, 0)
                self._nav_layout.addWidget(group_label)
            button = QPushButton(title)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, vid=view_id: self.navigate(vid))
            self._nav_layout.addWidget(button)
            self._nav_buttons[view_id] = button
            self._stack.addWidget(view)
            if index == 0:
                self._stack.setCurrentWidget(view)
                button.setChecked(True)

    def _build_shortcuts(self) -> None:
        for sequence, method in _SHORTCUTS:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(getattr(self, method))
        for index, (view_id, _title, _factory) in enumerate(VIEW_SPECS):
            if index > 9:
                break
            digit = (index + 1) % 10
            shortcut = QShortcut(QKeySequence(f"Ctrl+{digit}"), self)
            shortcut.activated.connect(lambda vid=view_id: self.navigate(vid))

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(_app_icon(), self)
        self._tray.setToolTip("AgentOps")
        menu = QMenu()
        show_action = QAction("Show AgentOps", self)
        show_action.triggered.connect(self._show_window)
        new_action = QAction("New Task...", self)
        new_action.triggered.connect(self.open_new_task)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_action)
        menu.addAction(new_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray.show()

    def _show_window(self) -> None:
        """Bring the window back from a minimized or hidden state."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # layout persistence
    # ------------------------------------------------------------------
    def restore_geometry(self) -> None:
        """Apply saved window geometry/maximize state before the first show."""
        geometry = self._settings.geometry
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except (ValueError, TypeError):
                pass
        if self._settings.maximized:
            self.showMaximized()

    def _persist_window_state(self) -> None:
        try:
            encoded = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self._settings.geometry = encoded
        except (AttributeError, ValueError):
            pass
        self._settings.maximized = bool(self.isMaximized())

    def _save_settings(self) -> None:
        save_settings(self._settings, self._settings_file)

    # ------------------------------------------------------------------
    # repository
    # ------------------------------------------------------------------
    def _sync_repository_label(self) -> None:
        path = self._repository or "No repository chosen"
        self._repo_label.setText(path)
        self._repo_label.setToolTip(path)

    def set_repository(self, path: str) -> None:
        cleaned = str(path).strip()
        if not cleaned or cleaned == self._repository:
            return
        self._repository = cleaned
        self._settings.touch_repository(cleaned)
        self._save_settings()
        self._sync_repository_label()
        self.toast("Repository switched", cleaned, "info")
        active = self._stack.currentWidget()
        refresh = getattr(active, "refresh", None)
        if callable(refresh):
            refresh()

    def choose_repository(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose repository", self._repository or ""
        )
        if chosen:
            self.set_repository(chosen)

    def update_settings(self, updates: dict) -> None:
        """Apply partial settings changes from views and persist them."""
        repository = updates.get("repository")
        if isinstance(repository, str) and repository:
            self.set_repository(repository)
        changed = False
        for key in ("sidebar_collapsed", "config_path", "last_view", "maximized"):
            if key in updates and getattr(self._settings, key) != updates[key]:
                setattr(self._settings, key, updates[key])
                changed = True
        if "sidebar_collapsed" in updates:
            self._apply_sidebar()
        if changed:
            self._save_settings()

    def _apply_sidebar(self) -> None:
        collapsed = bool(self._settings.sidebar_collapsed)
        self._sidebar.setVisible(not collapsed)
        self._sidebar_toggle.setText("Show sidebar" if collapsed else "Hide sidebar")

    def toggle_sidebar(self) -> None:
        self._settings.sidebar_collapsed = not self._settings.sidebar_collapsed
        self._apply_sidebar()
        self._save_settings()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def navigate(self, view_id: str) -> None:
        view = self._views.get(view_id)
        if view is None:
            return
        for button_id, button in self._nav_buttons.items():
            button.setChecked(button_id == view_id)
        if self._stack.currentWidget() is view:
            refresh = getattr(view, "refresh", None)
            if callable(refresh):
                refresh()
            return
        self._stack.setCurrentWidget(view)
        self._fade_stack()
        if self._settings.last_view != view_id:
            self._settings.last_view = view_id
            self._save_settings()
        refresh = getattr(view, "refresh", None)
        if callable(refresh):
            refresh()

    def _fade_stack(self) -> None:
        effect = QGraphicsOpacityEffect(self._stack)
        effect.setOpacity(0.0)
        self._stack.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self._stack)
        animation.setDuration(140)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)

        def clear_effect() -> None:
            # Keep the effect only for the fade itself. A permanently
            # installed opacity effect renders the whole stack through an
            # offscreen buffer; with the Settings scroll area that left
            # stale regions from the previous view until a repaint.
            if self._stack.graphicsEffect() is effect:
                self._stack.setGraphicsEffect(None)

        animation.finished.connect(clear_effect)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def refresh_active(self) -> None:
        active = self._stack.currentWidget()
        refresh = getattr(active, "refresh", None)
        if callable(refresh):
            refresh()

    def active_view(self) -> QWidget | None:
        return self._stack.currentWidget()

    # ------------------------------------------------------------------
    # detail navigation
    # ------------------------------------------------------------------
    def open_task(self, workflow_id: str, task_id: str = "") -> None:
        view = self._views.get("workflows")
        if view is None:
            return
        self.navigate("workflows")
        show = getattr(view, "show_workflow", None)
        if callable(show):
            show(workflow_id, task_id or None)

    def open_run(self, run_id: str) -> None:
        view = self._views.get("runs")
        if view is None:
            return
        self.navigate("runs")
        show = getattr(view, "show_run", None)
        if callable(show):
            show(run_id)

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def toast(self, title: str, message: str = "", kind: str = "info") -> None:
        self._toasts.show_toast(title, message, kind)

    def _notify_tray(self, title: str, message: str) -> None:
        if self.isHidden() or self.isMinimized():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information)

    # ------------------------------------------------------------------
    # command palette / shortcuts
    # ------------------------------------------------------------------
    def open_palette(self) -> None:
        commands: list[Command] = []
        for index, (view_id, title, _factory) in enumerate(VIEW_SPECS):
            if index > 9:
                break
            digit = (index + 1) % 10
            commands.append(Command(
                title=f"Go to {title}",
                run=lambda vid=view_id: self.navigate(vid),
                keywords=f"view open navigate {view_id}",
                shortcut=f"Ctrl+{digit}",
                group="Navigation",
            ))
        commands.extend([
            Command("New Task...", self.open_new_task,
                    keywords="create start task workflow", shortcut="Ctrl+N"),
            Command("Refresh current view", self.refresh_active,
                    keywords="reload update", shortcut="Ctrl+R"),
            Command("Toggle sidebar", self.toggle_sidebar,
                    keywords="navigation panel", shortcut="Ctrl+B"),
            Command("Change repository...", self.choose_repository,
                    keywords="repo folder path switch"),
        ])
        if self._active:
            commands.append(Command(
                "Cancel active operation", self.cancel_operation,
                keywords="stop abort kill"))
        for path in self._settings.recent_repositories:
            commands.append(Command(
                title=f"Open repository {path}",
                run=lambda p=path: self.set_repository(p),
                keywords="repo recent open",
                group="Repositories",
            ))
        self._palette.set_commands(commands)
        self._palette.open_centered()

    def open_new_task(self) -> None:
        if self._active:
            self.toast("Operation in progress",
                       "Cancel the running task before starting another.", "warning")
            return
        dialog = NewTaskDialog(self._views_ctx, self)
        if dialog.exec():
            values = dialog.values()
            self.start_task(
                str(values["description"]),
                repository=values.get("repository"),
                agent=values.get("agent"),
                verification_profile=values.get("verification_profile"),
            )
        dialog.deleteLater()

    # ------------------------------------------------------------------
    # task lifecycle
    # ------------------------------------------------------------------
    def start_task(self, description: str, repository: object = None,
                   agent: object = None, verification_profile: object = None) -> bool:
        target = str(repository or self._repository).strip()
        if not target:
            self.toast("No repository", "Choose a repository before starting a task.",
                       "error")
            return False
        description = str(description).strip()
        if not description:
            self.toast("Nothing to do", "Describe the task first.", "error")
            return False
        if target != self._repository:
            self.set_repository(target)
        directory = self._repository
        agent_name = str(agent) if agent else None
        profile = str(verification_profile) if verification_profile else None

        def preflight() -> object:
            payload = self._controller.list_workflows(directory, limit=1)
            rows = payload.get("workflows") if isinstance(payload, dict) else None
            return rows[0].get("id") if rows else None

        def launch(pre_id: object) -> None:
            self._pre_workflow_id = str(pre_id) if pre_id else None
            try:
                self._controller.run_task(
                    description, directory, self._bridge.operation_callback(),
                    agent=agent_name, verification_profile=profile,
                )
            except Exception as error:  # noqa: BLE001 - surface start failures
                self._set_status_idle()
                self.toast("Unable to start task", str(error), "error")
                return
            self._follow_enabled = True
            self._mark_operation(True, "Starting task...")

        self.submit("start-task", preflight, launch,
                    lambda message: launch(None))
        return True

    def cancel_operation(self) -> None:
        if not self._active:
            return
        try:
            self._controller.cancel()
        except Exception as error:  # noqa: BLE001 - surface cancel failures
            self.toast("Cancel failed", str(error), "error")
            return
        self._cancel_btn.setEnabled(False)
        self.toast("Cancellation requested", "The operation will stop shortly.", "info")

    # ------------------------------------------------------------------
    # operation events
    # ------------------------------------------------------------------
    def _on_operation_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        kind = str(event.get("kind") or "")
        active = self._stack.currentWidget()
        handler = getattr(active, "on_operation_event", None)
        if callable(handler):
            handler(event)

        if kind == "workflow-started":
            self._mark_operation(True, "Task running...")
            worktree = str(event.get("worktree") or "")
            self.toast("Workflow started",
                       Path(worktree).name if worktree else "", "success")
            self.navigate("workflows")
        elif kind == "workflow-result":
            self._follow_enabled = False
            workflow_id = str(event.get("workflow_id") or "")
            merged = bool(event.get("merged"))
            ready = bool(event.get("ready"))
            if ready and merged:
                self.toast("Workflow finished", "Changes merged into the base branch.",
                           "success")
            elif ready:
                self.toast("Workflow finished", "Ready, but nothing was merged.",
                           "warning")
            else:
                self.toast("Workflow finished",
                           "Not ready - open the workflow to inspect verification.",
                           "warning")
            self._notify_tray("AgentOps", f"Workflow {workflow_id} finished")
            if workflow_id:
                self.open_task(workflow_id)
        elif kind == "conflict":
            self._follow_enabled = False
            self.toast("Merge conflict", str(event.get("error") or ""), "error")
            workflow_id = str(event.get("workflow_id") or "")
            if workflow_id:
                self.open_task(workflow_id)
        elif kind == "cancelled":
            self.toast("Operation cancelled", "", "warning")
        elif kind == "error":
            self._follow_enabled = False
            self.toast("Operation failed", str(event.get("error") or ""), "error")
        elif kind == "result":
            succeeded = bool(event.get("succeeded"))
            self.toast(
                f"Run {'succeeded' if succeeded else 'failed'}",
                f"{event.get('agent') or ''} exit {event.get('exit_code')}",
                "success" if succeeded else "error",
            )
        elif kind == "thread-finished":
            self._follow_enabled = False
            self._set_status_idle()
            self._cancel_btn.setEnabled(True)
            self.refresh_active()

    def _mark_operation(self, active: bool, status: str = "") -> None:
        if active:
            if not self._active:
                self._active = True
                self._operation_started = time.monotonic()
                self._cancel_btn.show()
                self._new_task_btn.setEnabled(False)
                self._poll.start()
            self._status_dot.set_pulsing(True)
            if status:
                self._status_label.setText(status)
        else:
            self._set_status_idle()

    def _set_status_idle(self) -> None:
        self._active = False
        self._status_dot.set_pulsing(False)
        foreground, _background = status_colors("passed")
        self._status_dot.set_color(foreground)
        self._status_label.setText("Idle")
        self._cancel_btn.hide()
        self._cancel_btn.setEnabled(True)
        self._new_task_btn.setEnabled(True)
        self._poll.stop()

    def _on_poll(self) -> None:
        elapsed = time.monotonic() - self._operation_started
        self._status_label.setText(f"Running {format_duration(elapsed)}")
        self.refresh_active()
        if self._follow_enabled:
            self._check_follow()

    def _check_follow(self) -> None:
        """After starting a task, open the new workflow once it persists."""
        directory = self._repository
        pre_id = self._pre_workflow_id

        def list_recent() -> object:
            payload = self._controller.list_workflows(directory, limit=5)
            return payload.get("workflows") if isinstance(payload, dict) else None

        def on_rows(rows: object) -> None:
            if not self._follow_enabled or not isinstance(rows, list):
                return
            for row in rows:
                workflow_id = str(row.get("id") or "")
                if not workflow_id or workflow_id == pre_id:
                    continue
                self._follow_enabled = False
                self.open_task(workflow_id)
                return

        self.submit("follow", list_recent, on_rows)

    # ------------------------------------------------------------------
    # window lifecycle
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            initial = self._settings.last_view
            if initial not in self._views:
                initial = next(iter(self._views), "")
            if initial:
                self.navigate(initial)
        self._toasts.reposition()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._active:
            answer = QMessageBox.question(
                self, "Operation in progress",
                "A background operation is still running. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._persist_window_state()
        self._save_settings()
        self._bridge.shutdown()
        event.accept()
