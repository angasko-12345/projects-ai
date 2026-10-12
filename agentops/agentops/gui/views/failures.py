"""Failures surface: classification, severity, and recommended recovery."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout

from ..detail import FailureDetailPanel
from ..format import elide, format_timestamp, short_id
from ..widgets import TableColumn, danger_button
from .listdetail import ListDetailView


def _words(value: object) -> str:
    """``retry_same_agent`` -> ``retry same agent`` for dense table cells."""
    return str(value or "").replace("_", " ")


_COLUMNS = (
    TableColumn("id", "Failure", 150,
                format=lambda value: short_id(value, 8, tail=True)),
    TableColumn("severity", "Severity", 90),
    TableColumn("category", "Category", 150, format=_words),
    TableColumn("source", "Source", 110, format=_words),
    TableColumn("recovery_state", "Recovery", 130, format=_words),
    TableColumn("recommended_action", "Recommended action", 220,
                format=lambda value: elide(_words(value), 48)),
    TableColumn("primary_error", "Primary error", 240,
                format=lambda value: elide(str(value or ""), 52)),
    TableColumn("retryable", "Retryable", 90,
                format=lambda value: "yes" if value else "no"),
    TableColumn("repairable", "Repairable", 90,
                format=lambda value: "yes" if value else "no"),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
    TableColumn("workflow_id", "Workflow", 90, format=lambda value: short_id(value, 8)),
)


class FailuresView(ListDetailView):
    """Failures needing attention; selecting one shows classification detail."""

    view_id = "failures"
    title = "Failures"
    page_subtitle = "Classified failures with recommended recovery"
    columns = _COLUMNS
    empty_heading = "No failures recorded"
    empty_detail = "Classified failures and recovery actions appear here."
    search_placeholder = "Search failures..."

    # ------------------------------------------------------------------
    def _build_detail(self) -> FailureDetailPanel:
        return FailureDetailPanel(self.ctx)

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        self._severity = QComboBox()
        self._severity.addItem("All severities", None)
        for severity in ("critical", "high", "medium", "low"):
            self._severity.addItem(severity.title(), severity)
        toolbar.addWidget(self._severity)
        self._severity.currentIndexChanged.connect(self._apply_filters)
        self._actionable = QCheckBox("Actionable only")
        self._actionable.setToolTip(
            "Show only failures marked retryable or repairable."
        )
        self._actionable.toggled.connect(lambda _on: self._set_rows(self._all_rows))
        toolbar.addWidget(self._actionable)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected failure record")
        self._delete_btn.clicked.connect(self._delete_selected_failure)
        toolbar.addWidget(self._delete_btn)

    def _apply_filters(self, *_args: object) -> None:
        super()._apply_filters()
        self._table.set_column_filter("severity", self._severity.currentData() or "")

    def _build_hook(self) -> None:
        self._all_rows: list[dict] = []
        self._detail.changed.connect(self.refresh)
        self._table.selection_changed.connect(
            lambda _row: self._delete_btn.setEnabled(
                self._table.selected_row() is not None))
        self._delete_btn.setEnabled(False)

    def _delete_selected_failure(self) -> None:
        row = self._table.selected_row() or {}
        self.delete_selected(
            "failure",
            lambda repository, failure_id: self.ctx.controller.delete_failure(
                repository, failure_id),
            detail=str(row.get("primary_error") or row.get("category") or ""),
        )

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        return list(controller.list_recent_failures(repository, limit=100))

    def _set_rows(self, rows: list[dict]) -> None:
        self._all_rows = list(rows)
        if self._actionable.isChecked():
            rows = [
                row for row in self._all_rows
                if row.get("retryable") or row.get("repairable")
            ]
        super()._set_rows(rows)

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_failure(row)
