"""Artifacts surface: stored workflow artifacts with text preview."""

from __future__ import annotations

from ..detail import ArtifactDetailPanel
from ..format import format_size, format_timestamp, short_id
from ..widgets import TableColumn
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("id", "Artifact", 90, format=lambda value: short_id(value, 8)),
    TableColumn("name", "Name", 220),
    TableColumn("kind", "Kind", 150),
    TableColumn("size_bytes", "Size", 90, format=format_size,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else -1.0),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
    TableColumn("workflow_id", "Workflow", 90, format=lambda value: short_id(value, 8)),
    TableColumn("task_id", "Task", 90, format=lambda value: short_id(value, 8)),
    TableColumn("agent_run_id", "Run", 90, format=lambda value: short_id(value, 8)),
)


class ArtifactsView(ListDetailView):
    """Artifact list; selecting one previews its content."""

    view_id = "artifacts"
    title = "Artifacts"
    page_subtitle = "Plans, diffs, reports, and logs from workflow runs"
    columns = _COLUMNS
    empty_heading = "No artifacts stored"
    empty_detail = "Plans, diffs, reports, and logs land here as workflows run."
    search_placeholder = "Search artifacts..."

    def _build_detail(self) -> ArtifactDetailPanel:
        return ArtifactDetailPanel(self.ctx)

    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_artifacts(repository))

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_artifact(row)
