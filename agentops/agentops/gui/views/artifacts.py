"""Artifacts surface: stored workflow artifacts with text preview."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout

from ..detail import ArtifactDetailPanel

from ..format import format_size, format_timestamp, short_id
from ..widgets import TableColumn, danger_button
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

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        self._kind = QComboBox()
        self._kind.addItem("All kinds", None)
        toolbar.addWidget(self._kind)
        self._kind.currentIndexChanged.connect(self._apply_filters)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected artifact file and record")
        self._delete_btn.clicked.connect(self._delete_selected_artifact)
        toolbar.addWidget(self._delete_btn)

    def _build_hook(self) -> None:
        self._table.selection_changed.connect(
            lambda _row: self._delete_btn.setEnabled(
                self._table.selected_row() is not None))
        self._delete_btn.setEnabled(False)

    def _delete_selected_artifact(self) -> None:
        row = self._table.selected_row() or {}
        self.delete_selected(
            "artifact",
            lambda repository, artifact_id: self.ctx.controller.delete_artifact(
                repository, artifact_id),
            detail=str(row.get("name") or ""),
            extra="The stored file is removed too.",
        )

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_artifacts(repository))

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_artifact(row)

    def _set_rows(self, rows: list[dict]) -> None:
        super()._set_rows(rows)
        current = self._kind.currentData()
        self._kind.blockSignals(True)
        try:
            self._kind.clear()
            self._kind.addItem("All kinds", None)
            for kind in sorted({str(row.get("kind") or "unknown") for row in rows}):
                self._kind.addItem(kind, kind)
            index = self._kind.findData(current)
            self._kind.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._kind.blockSignals(False)
        self._table.set_column_filter("kind", self._kind.currentData() or "")

    def _apply_filters(self, *_args: object) -> None:
        super()._apply_filters()
        self._table.set_column_filter("kind", self._kind.currentData() or "")
