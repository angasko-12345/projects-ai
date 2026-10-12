"""Runs surface: searchable history of agent runs with detail drill-down."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout

from ..detail import RunDetailPanel
from ..format import format_duration, format_timestamp, short_id
from ..widgets import TableColumn, danger_button
from .listdetail import ListDetailView

_RUN_COLUMNS = (
    TableColumn("id", "Run", 90, format=lambda value: short_id(value, 8)),
    TableColumn("agent", "Agent", 130),
    TableColumn("role", "Role", 130),
    TableColumn("status", "Status", 110),
    TableColumn("duration_seconds", "Duration", 90, format=format_duration,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else -1.0),
    TableColumn("model", "Model", 130),
    TableColumn("task_id", "Task", 90, format=lambda value: short_id(value, 8)),
    TableColumn("workflow_id", "Workflow", 90, format=lambda value: short_id(value, 8)),
    TableColumn("started_at", "Started", 150, format=format_timestamp),
    TableColumn("ended_at", "Ended", 150, format=format_timestamp),
    TableColumn("exit_code", "Exit", 70),
)

_STATUSES = (
    "pending", "starting", "running", "completed",
    "failed", "cancelled", "timed_out", "terminated",
)
_ROLES = ("architecture", "implementation", "verification", "review", "debugging")


class RunsView(ListDetailView):
    """Filterable run history; selecting a run opens its detail panel."""

    view_id = "runs"
    title = "Runs"
    page_subtitle = "Persisted agent-run lifecycle records"
    columns = _RUN_COLUMNS
    empty_heading = "No agent runs yet"
    empty_detail = "Runs appear here as agents execute workflow tasks."
    search_placeholder = "Search runs..."

    # ------------------------------------------------------------------
    def _build_detail(self) -> RunDetailPanel:
        return RunDetailPanel(self.ctx)

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        self._status = QComboBox()
        self._status.addItem("All statuses", None)
        for status in _STATUSES:
            self._status.addItem(status.replace("_", " ").title(), status)
        toolbar.addWidget(self._status)

        self._role = QComboBox()
        self._role.addItem("All roles", None)
        for role in _ROLES:
            self._role.addItem(role.title(), role)
        toolbar.addWidget(self._role)

        self._status.currentIndexChanged.connect(self._apply_filters)
        self._role.currentIndexChanged.connect(self._apply_filters)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected run and its verifications")
        self._delete_btn.clicked.connect(self._delete_selected_run)
        toolbar.addWidget(self._delete_btn)

    def _build_hook(self) -> None:
        self._table.selection_changed.connect(
            lambda _row: self._delete_btn.setEnabled(
                self._table.selected_row() is not None))
        self._delete_btn.setEnabled(False)

    def _delete_selected_run(self) -> None:
        row = self._table.selected_row() or {}
        agent = str(row.get("agent") or "")
        role = str(row.get("role") or "")
        detail = f"{agent} - {role}".strip(" -")
        self.delete_selected(
            "agent run",
            lambda repository, run_id: self.ctx.controller.delete_agent_run(
                repository, run_id),
            detail=detail,
            extra="Linked verification runs go with it; failure rows stay readable.",
        )

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_recent_agent_runs(repository, limit=100))

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_run(row)

    def _fetch_one(self, repository: str, item_id: str) -> dict | None:
        controller = self.ctx.controller
        return controller.get_agent_run(repository, item_id)

    def show_run(self, run_id: str) -> None:
        self.show_item(run_id)

    def _apply_filters(self, *_args: object) -> None:
        super()._apply_filters()
        self._table.set_column_filter("status", self._status.currentData() or "")
        self._table.set_column_filter("role", self._role.currentData() or "")
