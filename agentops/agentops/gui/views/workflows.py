"""Workflows surface: master list plus live pipeline, tasks, runs,
artifacts, per-task detail, cancel/recover actions, and worktree context.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..detail import TaskDetailPanel, _confirm
from ..format import elide, format_duration, format_timestamp, short_id
from ..widgets import (
    EmptyState,
    KeyValueGrid,
    PipelineBar,
    SectionHeader,
    TablePanel,
    TableColumn,
    danger_button,
    ghost_button,
)
from .base import BaseView

_KNOWN_ROLES = (
    "architecture", "implementation", "verification", "review", "debugging",
)
_ROLE_LABELS = {
    "architecture": "Plan",
    "implementation": "Implement",
    "verification": "Verify",
    "review": "Review",
    "debugging": "Debug",
}

_LIST_COLUMNS = (
    TableColumn("status", "Status", 90),
    TableColumn("id", "ID", 70, format=lambda value: short_id(value, 6)),
    TableColumn("description", "Description", 200,
                format=lambda value: elide(str(value or ""), 40)),
    TableColumn("created_at", "Created", 130, format=format_timestamp),
)
_TASK_COLUMNS = (
    TableColumn("id", "Task", 80, format=lambda value: short_id(value, 6)),
    TableColumn("role", "Role", 130),
    TableColumn("status", "Status", 100),
    TableColumn("description", "Description", 240,
                format=lambda value: elide(str(value or ""), 56)),
    TableColumn("assigned_agent", "Agent", 130),
    TableColumn("attempts", "Attempts", 80),
    TableColumn("verified", "Verified", 80,
                format=lambda value: "yes" if value else "no"),
    TableColumn("result", "Result", 160,
                format=lambda value: elide(str(value or ""), 36)),
)
_RUN_COLUMNS = (
    TableColumn("id", "Run", 80, format=lambda value: short_id(value, 6)),
    TableColumn("agent", "Agent", 130),
    TableColumn("role", "Role", 120),
    TableColumn("status", "Status", 100),
    TableColumn("duration_seconds", "Duration", 90, format=format_duration,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else -1.0),
    TableColumn("started_at", "Started", 150, format=format_timestamp),
)
_ARTIFACT_COLUMNS = (
    TableColumn("name", "Name", 220),
    TableColumn("kind", "Kind", 150),
    TableColumn("size_bytes", "Size", 90),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
)


def _task_group_status(tasks: list[dict]) -> str:
    statuses = {str(task.get("status") or "") for task in tasks}
    if "running" in statuses:
        return "running"
    if statuses & {"failed", "blocked"}:
        return "failed"
    if tasks and statuses <= {"passed"}:
        return "passed"
    return "pending"


def _task_progress(tasks: list[dict]) -> str:
    if not tasks:
        return "no tasks"
    passed = sum(1 for task in tasks if task.get("status") == "passed")
    failed = sum(1 for task in tasks if task.get("status") in ("failed", "blocked"))
    text = f"{passed}/{len(tasks)} done"
    if failed:
        text += f"  ·  {failed} failed"
    return text


class _Pane(QWidget):
    """Framed container so QSplitter children paint the card surface."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)


class WorkflowsView(BaseView):
    """Master workflow list driving a live pipeline/task/run workspace."""

    view_id = "workflows"
    title = "Workflows"

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        splitter = QSplitter()

        # Left: workflow list.
        left = _Pane()
        left_body = QVBoxLayout(left)
        left_body.setContentsMargins(12, 12, 12, 12)
        left_body.setSpacing(8)
        left_body.addWidget(SectionHeader("Workflows", "Newest first"))
        self._list = TablePanel(
            _LIST_COLUMNS, "No workflows yet",
            "Start a task to create your first workflow."
        )
        left_body.addWidget(self._list)
        splitter.addWidget(left)

        # Right: selected workflow workspace.
        self._stack = QStackedWidget()
        self._placeholder = EmptyState(
            "No workflow selected",
            "Pick a workflow to inspect its pipeline, tasks, runs, and artifacts."
        )
        self._stack.addWidget(self._placeholder)
        detail = _Pane()
        body = QVBoxLayout(detail)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(10)

        self._header = SectionHeader("Workflow", "")
        self._cancel = danger_button("Cancel")
        self._cancel.clicked.connect(self._cancel_operation)
        self._cancel.setVisible(False)
        self._header.add_action(self._cancel)
        self._recover = ghost_button("Recover interrupted")
        self._recover.clicked.connect(self._recover_workflow)
        self._header.add_action(self._recover)
        body.addWidget(self._header)

        self._grid = KeyValueGrid(columns=3)
        body.addWidget(self._grid)
        self._pipeline = PipelineBar()
        body.addWidget(self._pipeline)

        tables = QSplitter()
        tables.setOrientation(tables.orientation().Vertical)
        self._tasks = TablePanel(
            _TASK_COLUMNS, "No tasks", "This workflow has no tasks."
        )
        self._artifacts = TablePanel(
            _ARTIFACT_COLUMNS, "No artifacts",
            "Plans, diffs, reports, and logs appear here."
        )
        self._runs = TablePanel(
            _RUN_COLUMNS, "No runs", "Agent runs appear here once started."
        )
        self._task_detail = TaskDetailPanel(self.ctx)
        for widget in (self._tasks, self._artifacts, self._runs, self._task_detail):
            tables.addWidget(widget)
        tables.setStretchFactor(0, 3)
        tables.setStretchFactor(1, 1)
        tables.setStretchFactor(2, 2)
        tables.setStretchFactor(3, 2)
        body.addWidget(tables, stretch=1)
        self._stack.addWidget(detail)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, stretch=1)

        self._list.selection_changed.connect(self._pick_workflow)
        self._tasks.selection_changed.connect(self._pick_task)
        self._tasks.activated.connect(self._pick_task)
        self._runs.activated.connect(
            lambda row: self.ctx.open_run(str(row.get("id") or ""))
        )
        self._rows: list[dict] = []
        self._current_id: str | None = None
        self._pending_task_id: str | None = None
        self._selected_task_id: str | None = None

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._rows = []
            self._list.set_rows([])
            self._cancel.setVisible(False)
            return
        controller = self.ctx.controller
        self.submit(
            "list",
            lambda: controller.list_workflows(repository, limit=25),
            self._on_list,
        )
        if self._current_id:
            self._load_workflow()
        else:
            self._cancel.setVisible(bool(self.ctx.operation_active()))

    def show_workflow(self, workflow_id: str, task_id: str = "") -> None:
        workflow_id = str(workflow_id or "")
        if not workflow_id:
            return
        self._current_id = workflow_id
        self._pending_task_id = str(task_id or "") or None
        self._select_in_list(workflow_id)
        self._load_workflow()

    def on_operation_event(self, event: dict) -> None:
        kind = str(event.get("kind") or "")
        if kind in ("workflow-started", "workflow-result", "result",
                    "conflict", "cancelled", "error", "thread-finished"):
            self.refresh()

    # ------------------------------------------------------------------
    def _load_workflow(self) -> None:
        if not self._current_id:
            return
        repository = self.repository()
        controller = self.ctx.controller
        workflow_id = self._current_id
        self.submit(
            "workflow",
            lambda: controller.get_workflow(repository, workflow_id),
            self._on_workflow,
        )
        self.submit(
            "artifacts",
            lambda: controller.list_artifacts(repository, workflow_id=workflow_id),
            lambda rows: self._artifacts.set_rows(list(rows or [])),
        )
        self._cancel.setVisible(bool(self.ctx.operation_active()))

    def _on_list(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._rows = list(payload.get("workflows") or [])  # type: ignore[arg-type]
        self._list.set_rows(self._rows)
        if self._current_id:
            self._select_in_list(self._current_id)

    def _select_in_list(self, workflow_id: str) -> None:
        proxy = self._list.proxy
        for index in range(proxy.rowCount()):
            record = self._list.row_of(proxy.index(index, 0))
            if record and str(record.get("id") or "") == workflow_id:
                if self._list.selected_row() != record:
                    self._list.select_row_index(index)
                return

    def _on_workflow(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            return
        if str(workflow.get("id") or "") != self._current_id:
            return  # stale response for a previously selected workflow
        tasks = list(payload.get("tasks") or [])
        runs = list(payload.get("runs") or [])
        worktree = payload.get("worktree_ref")
        if not isinstance(worktree, dict):
            worktree = {}

        self._header.set_title(f"Workflow {short_id(workflow.get('id') or '', 8)}")
        self._header.set_subtitle(str(workflow.get("status") or ""))
        self._set_grid(workflow, tasks, runs, worktree)
        self._pipeline.set_stages(self._stages(workflow, tasks))
        self._tasks.set_rows(tasks)
        self._runs.set_rows(runs)
        self._stack.setCurrentWidget(self._stack.widget(1))
        if self._pending_task_id:
            self._selected_task_id = self._pending_task_id
            self._pending_task_id = None
        if self._selected_task_id:
            self._reselect_task(self._selected_task_id)

    def _reselect_task(self, task_id: str) -> None:
        proxy = self._tasks.proxy
        for index in range(proxy.rowCount()):
            record = self._tasks.row_of(proxy.index(index, 0))
            if record and str(record.get("id") or "") == task_id:
                if self._tasks.selected_row() != record:
                    self._tasks.select_row_index(index)
                return
        self._selected_task_id = None

    def _set_grid(self, workflow: dict, tasks: list[dict],
                  runs: list[dict], worktree: dict) -> None:
        grid = self._grid
        grid.clear()
        grid.add_row("Status", workflow.get("status"))
        grid.add_row("Tasks", _task_progress(tasks))
        grid.add_row("Runs", f"{len(runs)} recorded")
        grid.add_row("Workflow", short_id(workflow.get("id") or "", 12),
                     mono_value=True)
        grid.add_row("Created", format_timestamp(str(workflow.get("created_at") or "")))
        grid.add_row("Updated", format_timestamp(str(workflow.get("updated_at") or "")))
        grid.add_row("Description", workflow.get("description"))
        grid.add_row(
            "Worktree",
            (f"{worktree.get('branch')}  ·  {worktree.get('path')}"
             if worktree else "none"),
            mono_value=bool(worktree),
        )

    def _stages(self, workflow: dict, tasks: list[dict]) -> list[dict]:
        by_role: dict[str, list[dict]] = {}
        for task in tasks:
            by_role.setdefault(str(task.get("role") or "other"), []).append(task)
        roles = list(by_role)
        custom = any(role not in _KNOWN_ROLES for role in roles)
        order = roles if custom else [
            role for role in _KNOWN_ROLES if role in by_role
        ]
        stages: list[dict] = []
        for role in order:
            group = by_role[role]
            label = role.title() if custom else _ROLE_LABELS.get(role, role.title())
            stages.append({
                "key": role,
                "label": label,
                "subtitle": _task_progress(group),
                "status": _task_group_status(group),
            })
        status = str(workflow.get("status") or "").lower()
        finalize = ("passed" if status in ("completed", "passed", "merged")
                    else "failed" if status in ("failed", "cancelled")
                    else "running" if status == "running" else "pending")
        stages.append({
            "key": "finalize",
            "label": "Finalize",
            "subtitle": status or "pending",
            "status": finalize,
        })
        return stages

    # ------------------------------------------------------------------
    def _pick_workflow(self, row: object) -> None:
        if isinstance(row, dict):
            workflow_id = str(row.get("id") or "")
            if workflow_id and workflow_id != self._current_id:
                self.show_workflow(workflow_id)

    def _pick_task(self, row: object) -> None:
        if isinstance(row, dict):
            self._selected_task_id = str(row.get("id") or "") or None
            self._task_detail.set_task(row)

    def _cancel_operation(self) -> None:
        controller = self.ctx.controller
        controller.cancel()
        self.ctx.toast("Cancellation requested", "Stopping the active operation.",
                       "warning")
        self._cancel.setVisible(False)

    def _recover_workflow(self) -> None:
        if not self._current_id:
            return
        if not _confirm(
            "Recover interrupted runs",
            "Mark interrupted runs, verifications, and tasks of this workflow "
            "as terminated so the queue can proceed?",
            danger=True,
        ):
            return
        repository = self.repository()
        controller = self.ctx.controller
        workflow_id = self._current_id

        def run() -> object:
            return controller.recover_interrupted(repository, workflow_id)

        def done(result: object) -> None:
            if isinstance(result, dict) and result:
                counts = ", ".join(f"{key}: {value}" for key, value in result.items())
            else:
                counts = "nothing to recover"
            self.ctx.toast("Recovery applied", counts, "success")
            self.refresh()

        self.submit("recover", run, done)
