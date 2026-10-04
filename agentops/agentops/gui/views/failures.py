"""Failures surface: classification, severity, and recommended recovery."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from ..detail import FailureDetailPanel
from ..format import elide, format_timestamp, short_id
from ..widgets import TableColumn
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("id", "Failure", 90, format=lambda value: short_id(value, 8)),
    TableColumn("severity", "Severity", 90),
    TableColumn("category", "Category", 150),
    TableColumn("source", "Source", 110),
    TableColumn("recovery_state", "Recovery", 110),
    TableColumn("recommended_action", "Recommended action", 220,
                format=lambda value: elide(str(value or ""), 48)),
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
        self._actionable = QCheckBox("Actionable only")
        self._actionable.setToolTip(
            "Show only failures marked retryable or repairable."
        )
        self._actionable.toggled.connect(lambda _on: self._set_rows(self._all_rows))
        toolbar.addWidget(self._actionable)

    def _build_hook(self) -> None:
        self._all_rows: list[dict] = []
        self._detail.changed.connect(self.refresh)

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
