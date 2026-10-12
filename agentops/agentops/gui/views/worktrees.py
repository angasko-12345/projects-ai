"""Worktrees surface: Git worktrees with merge/retry/cleanup actions."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QMessageBox

from ..detail import WorktreeDetailPanel
from ..format import elide, short_id
from ..widgets import TableColumn, danger_button
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("branch", "Branch", 200),
    TableColumn("status", "Status", 170,
                format=lambda value: str(value) if value else "clean"),
    TableColumn("head", "Head", 100, format=lambda value: short_id(value, 8)),
    TableColumn("managed", "Managed", 90,
                format=lambda value: "yes" if value else "no"),
    TableColumn("path", "Path", 320, format=lambda value: elide(str(value or ""), 70)),
    TableColumn("id", "ID", 90, format=lambda value: short_id(value, 8)),
)


class WorktreesView(ListDetailView):
    """Worktree list with merge/retry/cleanup actions in the detail panel."""

    view_id = "worktrees"
    title = "Worktrees"
    page_subtitle = "Isolated worktrees created by workflow runs"
    columns = _COLUMNS
    empty_heading = "No worktrees"
    empty_detail = "Workflows create Git worktrees for isolated agent work."
    search_placeholder = "Search worktrees..."

    # ------------------------------------------------------------------
    def _build_detail(self) -> WorktreeDetailPanel:
        return WorktreeDetailPanel(self.ctx)

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        self._delete_branch = QCheckBox("Delete unmerged branch on cleanup")
        self._delete_branch.setToolTip(
            "Ask cleanup to delete the worktree branch when its work is not merged."
        )
        self._delete_branch.toggled.connect(self._refill)
        toolbar.addWidget(self._delete_branch)
        self._managed = QComboBox()
        self._managed.addItem("All worktrees", None)
        self._managed.addItem("Managed", "yes")
        self._managed.addItem("Unmanaged", "no")
        toolbar.addWidget(self._managed)
        self._managed.currentIndexChanged.connect(self._apply_filters)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected worktree from disk")
        self._delete_btn.clicked.connect(self._delete_selected_worktree)
        toolbar.addWidget(self._delete_btn)

    def _build_hook(self) -> None:
        self._current: dict | None = None
        self._detail.changed.connect(self.refresh)
        self._table.selection_changed.connect(
            lambda _row: self._delete_btn.setEnabled(
                self._table.selected_row() is not None))
        self._delete_btn.setEnabled(False)

    def _apply_filters(self, *_args: object) -> None:
        super()._apply_filters()
        self._table.set_column_filter("managed", self._managed.currentData() or "")

    def _delete_selected_worktree(self) -> None:
        row = self._table.selected_row()
        if not row:
            return
        path = str(row.get("path") or row.get("id") or "")
        delete_branch = self._delete_branch.isChecked()
        message = f"Remove this worktree from disk?\n\n{path}"
        if delete_branch:
            message += "\n\nIts unmerged branch is deleted too."
        answer = QMessageBox.question(self, "Delete worktree", message)
        if answer != QMessageBox.StandardButton.Yes:
            return
        repository = self.repository()

        def done(_result: object) -> None:
            self._current = None
            self.fill_detail(None)
            self.ctx.toast("Worktree removed", "", "success")
            self.refresh()

        self.submit(
            "delete-worktree",
            lambda: self.ctx.controller.cleanup_worktree(
                repository, path, delete_branch),
            done,
            lambda message: self.ctx.toast("Delete failed", message, "error"),
        )

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        repository = self.repository()
        rows = list(controller.list_worktrees(repository))
        for row in rows:
            # Worktrees are identified by their path (no workflow id).
            row["id"] = str(row.get("path") or row.get("id") or "")
        return rows

    def fill_detail(self, row: dict | None) -> None:
        self._current = row
        self._detail.set_worktree(
            row, delete_unmerged_branch=self._delete_branch.isChecked()
        )

    def _refill(self, *_args: object) -> None:
        if self._current is not None:
            self.fill_detail(self._current)
