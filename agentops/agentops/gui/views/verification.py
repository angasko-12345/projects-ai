"""Verification surface: run history with check details and reports."""

from __future__ import annotations

from ..format import format_duration, format_timestamp, short_id
from ..widgets import TableColumn
from ..detail import VerificationDetailPanel
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("id", "Run", 90, format=lambda value: short_id(value, 8)),
    TableColumn("profile_name", "Profile", 150),
    TableColumn("mode", "Mode", 90),
    TableColumn("status", "Status", 110),
    TableColumn("total_checks", "Checks", 70),
    TableColumn("passed_checks", "Passed", 70),
    TableColumn("failed_checks", "Failed", 70),
    TableColumn("skipped_checks", "Skipped", 80),
    TableColumn("duration_seconds", "Duration", 90, format=format_duration,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else -1.0),
    TableColumn("started_at", "Started", 150, format=format_timestamp),
    TableColumn("workflow_id", "Workflow", 90, format=lambda value: short_id(value, 8)),
    TableColumn("task_id", "Task", 90, format=lambda value: short_id(value, 8)),
)


class VerificationView(ListDetailView):
    """Verification runs; selecting one loads its checks and report."""

    view_id = "verification"
    title = "Verification"
    columns = _COLUMNS
    empty_heading = "No verification runs yet"
    empty_detail = "Verification runs appear here when workflow tasks verify."
    search_placeholder = "Search verification runs..."

    def _build_detail(self) -> VerificationDetailPanel:
        return VerificationDetailPanel(self.ctx)

    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_recent_verification_runs(repository, limit=100))

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_verification(row)

    def _fetch_one(self, repository: str, item_id: str) -> dict | None:
        controller = self.ctx.controller
        payload = controller.get_verification_run(repository, item_id)
        run = payload.get("run") if isinstance(payload, dict) else None
        return run if isinstance(run, dict) else None
