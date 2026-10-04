"""Worktrees surface: Git worktrees with merge/retry/cleanup actions."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from ..detail import WorktreeDetailPanel
from ..format import elide, short_id
from ..widgets import TableColumn
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

    def _build_hook(self) -> None:
        self._current: dict | None = None
        self._detail.changed.connect(self.refresh)

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
