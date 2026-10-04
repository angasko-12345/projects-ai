"""Tasks surface: all tasks across workflows with per-task detail."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout

from ..detail import TaskDetailPanel
from ..format import elide, format_timestamp, short_id
from ..widgets import TableColumn
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("id", "Task", 90, format=lambda value: short_id(value, 8)),
    TableColumn("description", "Description", 260,
                format=lambda value: elide(str(value or ""), 64)),
    TableColumn("role", "Role", 130),
    TableColumn("status", "Status", 110),
    TableColumn("assigned_agent", "Agent", 140),
    TableColumn("attempts", "Attempts", 90),
    TableColumn("verified", "Verified", 80,
                format=lambda value: "yes" if value else "no"),
    TableColumn("result", "Result", 180,
                format=lambda value: elide(str(value or ""), 40)),
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
