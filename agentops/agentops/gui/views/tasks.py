"""Tasks surface: all tasks across workflows with per-task detail."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QMessageBox

from ..detail import TaskDetailPanel
from ..format import elide, format_timestamp, short_id
from ..widgets import TableColumn, danger_button, ghost_button
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("id", "Task", 90, format=lambda value: short_id(value, 8)),
    TableColumn(
        "description", "Description", 260, format=lambda value: elide(str(value or ""), 64)
    ),
    TableColumn("role", "Role", 130),
    TableColumn("status", "Status", 110),
    TableColumn("assigned_agent", "Agent", 140),
    TableColumn("attempts", "Attempts", 90),
    TableColumn("verified", "Verified", 80, format=lambda value: "yes" if value else "no"),
    TableColumn("result", "Result", 180, format=lambda value: elide(str(value or ""), 40)),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
    TableColumn("updated_at", "Updated", 150, format=format_timestamp),
    TableColumn("workflow_id", "Workflow", 90, format=lambda value: short_id(value, 8)),
)

_STATUSES = ("pending", "running", "passed", "failed", "blocked")


class TasksView(ListDetailView):
    """All tasks newest-first; selecting one shows runs/verifications detail."""

    view_id = "tasks"
    title = "Tasks"
    page_subtitle = "All tasks across workflows"
    columns = _COLUMNS
    empty_heading = "No tasks yet"
    empty_detail = "Tasks appear here when workflows break work into steps."
    search_placeholder = "Search tasks..."

    # ------------------------------------------------------------------
    def _build_detail(self) -> TaskDetailPanel:
        return TaskDetailPanel(self.ctx)

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        self._status = QComboBox()
        self._status.addItem("All statuses", None)
        for status in _STATUSES:
            self._status.addItem(status.title(), status)
        toolbar.addWidget(self._status)
        self._status.currentIndexChanged.connect(self._apply_filters)
        self._rename_btn = ghost_button("Rename...")
        self._rename_btn.setToolTip("Edit the selected task's description")
        self._rename_btn.clicked.connect(self._rename_selected)
        toolbar.addWidget(self._rename_btn)
        self._category_btn = ghost_button("Category...")
        self._category_btn.setToolTip("Move the selected task to another role")
        self._category_btn.clicked.connect(self._recategorize_selected)
        toolbar.addWidget(self._category_btn)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected task")
        self._delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._delete_btn)

    def _build_hook(self) -> None:
        self._table.selection_changed.connect(lambda _row: self._sync_actions())
        self._sync_actions()

    def _sync_actions(self) -> None:
        has_selection = self._table.selected_row() is not None
        for button in (self._rename_btn, self._category_btn, self._delete_btn):
            button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_tasks(repository, limit=100))

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_task(row)

    def _apply_filters(self, *_args: object) -> None:
        super()._apply_filters()
        self._table.set_column_filter("status", self._status.currentData() or "")

    # -- mutations ------------------------------------------------------
    def _mutate(
        self, key: str, work, success: str, failure: str, clear_selection: bool = False
    ) -> None:
        """Run a task mutation off-thread; refresh on success, toast either way."""

        def done(result: object) -> None:
            detail = ""
            if isinstance(result, dict):
                detail = elide(result.get("description"), 80)
            if clear_selection:
                self._selected_id = None
            self.ctx.toast(success, detail, "success")
            self.refresh()

        self.submit(
            key,
            work,
            done,
            lambda message: self.ctx.toast(failure, message, "error"),
        )

    def _rename_selected(self) -> None:
        row = self._table.selected_row()
        if not row:
            return
        task_id = str(row.get("id") or "")
        text, accepted = QInputDialog.getText(
            self, "Rename task", "Description:", text=str(row.get("description") or "")
        )
        if not accepted:
            return
        repository = self.repository()
        self._mutate(
            "rename-task",
            lambda: self.ctx.controller.rename_task(repository, task_id, text),
            "Task renamed",
            "Rename failed",
        )

    def _recategorize_selected(self) -> None:
        row = self._table.selected_row()
        if not row:
            return
        roles = list(
            getattr(
                self.ctx.controller,
                "TASK_ROLES",
                ("architecture", "implementation", "verification", "review", "debugging"),
            )
        )
        current = str(row.get("role") or "")
        role, accepted = QInputDialog.getItem(
            self,
            "Change category",
            "Role:",
            roles,
            current=roles.index(current) if current in roles else 0,
            editable=False,
        )
        if not accepted:
            return
        task_id = str(row.get("id") or "")
        repository = self.repository()
        self._mutate(
            "recategorize-task",
            lambda: self.ctx.controller.recategorize_task(repository, task_id, role),
            "Task recategorized",
            "Recategorize failed",
        )

    def _delete_selected(self) -> None:
        row = self._table.selected_row()
        if not row:
            return
        task_id = str(row.get("id") or "")
        answer = QMessageBox.question(
            self,
            "Delete task",
            f"Delete this task?\n\n{elide(row.get('description'), 80)}\n\n"
            "Recorded runs keep their history.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        repository = self.repository()
        self._mutate(
            "delete-task",
            lambda: self.ctx.controller.delete_task(repository, task_id),
            "Task deleted",
            "Delete failed",
            clear_selection=True,
        )
