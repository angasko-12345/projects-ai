"""Control center: the workflow as an operation, not a list of records.

Reads the existing controller surface (no backend changes) and renders it
through :mod:`agentops.gui.control_center`, which owns every projection so the
widgets here only lay out what the controller already reported.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import control_center as projection
from ..detail import _confirm
from ..format import elide, format_duration, format_size, format_timestamp, short_id
from ..platform import open_path_external
from ..threads import ThreadList, load_threads
from ..tokens import DARK
from ..widgets import (
    Badge,
    KeyValueGrid,
    LiveCard,
    LogText,
    PageHeader,
    SectionHeader,
    StageFlow,
    TablePanel,
    SearchBox,
    TableColumn,
    Tally,
    danger_button,
    ghost_button,
)
from .base import BaseView

_WORKFLOW_COLUMNS = (
    TableColumn("status", "Status", 100),
    TableColumn("id", "Workflow", 90, format=lambda v: short_id(v, 8)),
    TableColumn(
        "description", "Description", 300, stretch=True, format=lambda v: elide(str(v or ""), 60)
    ),
    TableColumn("created_at", "Created", 140, format=format_timestamp),
)

_TASK_COLUMNS = (
    TableColumn("status", "Status", 100),
    TableColumn("role", "Role", 120),
    TableColumn("assigned_agent", "Agent", 120),
    TableColumn("attempts", "Attempts", 80),
    TableColumn("verified", "Verified", 80, format=lambda v: "yes" if v else "no"),
    # Stretch the widest text column so the table fills its pane instead of
    # leaving an unstyled strip of viewport beside the last section.
    TableColumn(
        "description", "Description", 260, stretch=True, format=lambda v: elide(str(v or ""), 56)
    ),
    TableColumn("id", "Task", 90, format=lambda v: short_id(v, 8)),
)

_RUN_COLUMNS = (
    TableColumn("status", "Status", 100),
    TableColumn("agent", "Agent", 120),
    TableColumn("model", "Model", 130),
    TableColumn("role", "Role", 110),
    TableColumn("duration_seconds", "Duration", 90, format=format_duration),
    TableColumn("exit_code", "Exit", 60),
    TableColumn("id", "Run", 90, format=lambda v: short_id(v, 8), stretch=True),
)

_VERIFICATION_COLUMNS = (
    TableColumn("overall_status", "Status", 110),
    TableColumn("profile_name", "Profile", 130),
    TableColumn("passed_checks", "Passed", 80),
    TableColumn("failed_checks", "Failed", 70),
    TableColumn("skipped_checks", "Skipped", 80),
    TableColumn("total_checks", "Checks", 70),
    TableColumn("id", "Run", 90, format=lambda v: short_id(v, 8), stretch=True),
)

_CHECK_COLUMNS = (
    TableColumn("status", "Status", 100),
    TableColumn("name", "Check", 200),
    TableColumn("check_class", "Class", 120),
    TableColumn("required", "Required", 80, format=lambda v: "yes" if v else "no"),
    TableColumn("exit_code", "Exit", 60),
    TableColumn(
        "failure_reason", "Reason", 240, stretch=True, format=lambda v: elide(str(v or ""), 70)
    ),
)

_FAILURE_COLUMNS = (
    TableColumn("severity", "Severity", 100),
    TableColumn("category", "Category", 150, format=lambda v: str(v or "").replace("_", " ")),
    TableColumn(
        "recommended_action", "Action", 160, format=lambda v: str(v or "").replace("_", " ")
    ),
    TableColumn("retryable", "Retryable", 90, format=lambda v: "yes" if v else "no"),
    TableColumn("repairable", "Repairable", 90, format=lambda v: "yes" if v else "no"),
    TableColumn(
        "primary_error", "Error", 260, stretch=True, format=lambda v: elide(str(v or ""), 60)
    ),
)

_ARTIFACT_COLUMNS = (
    TableColumn("kind", "Kind", 140),
    TableColumn("name", "Name", 240, stretch=True),
    TableColumn("size_bytes", "Size", 90, format=format_size),
    TableColumn("created_at", "Created", 140, format=format_timestamp),
)


class _Pane(QWidget):
    """Framed container so QSplitter children paint the card surface."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)


class WorkflowsView(BaseView):
    """The control center: one workflow, end to end, while it runs."""

    view_id = "workflows"
    title = "Workflows"

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        page = DARK.space_page
        root.setContentsMargins(page, page, page, page)
        root.setSpacing(DARK.space_md)

        self._page_header = PageHeader(self.title, "Live stages, tasks, runs, and merge readiness")
        self._status_badge = Badge()
        self._page_header.add_action(self._status_badge)
        self._cancel = danger_button("Cancel run")
        self._cancel.clicked.connect(self._cancel_operation)
        self._page_header.add_action(self._cancel)
        self._recover = ghost_button("Recover interrupted")
        self._recover.clicked.connect(self._recover_workflow)
        self._page_header.add_action(self._recover)
        self._rename_btn = ghost_button("Rename...")
        self._rename_btn.setToolTip("Edit the selected workflow's description")
        self._rename_btn.clicked.connect(self._rename_workflow)
        self._page_header.add_action(self._rename_btn)
        self._delete_btn = danger_button("Delete")
        self._delete_btn.setToolTip("Remove the selected workflow and its records")
        self._delete_btn.clicked.connect(self._delete_workflow)
        self._page_header.add_action(self._delete_btn)
        root.addWidget(self._page_header)
        # The search, status filter and thread list live in the shell's
        # contextual sidebar (sidebar_parts); _list stays as the hidden
        # table behind the filters, selection and rename/delete.
        self._sidebar_filters = QWidget()
        list_toolbar = QHBoxLayout(self._sidebar_filters)
        list_toolbar.setContentsMargins(0, 0, 0, 0)
        list_toolbar.setSpacing(DARK.space_sm)
        self._wf_search = SearchBox("Search")
        self._wf_search.setMinimumWidth(0)
        self._wf_search.textChanged.connect(self._apply_list_filters)
        list_toolbar.addWidget(self._wf_search, stretch=1)
        self._wf_status = QComboBox()
        self._wf_status.setProperty("scope", True)  # flat, like the repo scope
        self._wf_status.setToolTip("Filter by status")
        self._wf_status.setAccessibleName("Status filter")
        # Compact: "Running" fits; longer statuses elide (the popup shows all).
        self._wf_status.setFixedWidth(
            self._wf_status.fontMetrics().horizontalAdvance("Running") + DARK.space_xxl + 8
        )
        self._wf_status.addItem("All", None)
        for status in (
            "pending",
            "running",
            "passed",
            "failed",
            "blocked",
            "completed",
            "merged",
            "cancelled",
        ):
            self._wf_status.addItem(status.title(), status)
        list_toolbar.addWidget(self._wf_status)
        self._wf_status.currentIndexChanged.connect(self._apply_list_filters)
        self._threads = ThreadList(self.ctx)
        self._threads.workflow_selected.connect(self._on_thread_selected)

        splitter = QSplitter()
        self._list = TablePanel(
            _WORKFLOW_COLUMNS, "No workflows yet", "Start a task to see a workflow here."
        )
        self._list.selection_changed.connect(self._pick_workflow)
        self._list.activated.connect(self._pick_workflow)
        self._list.hide()
        splitter.addWidget(self._list)

        # The body scrolls: without it a short pane squeezed the StageFlow
        # cards to slivers and clipped their stage, agent and duration lines.
        self._body = _Pane()
        pane = QVBoxLayout(self._body)
        pane.setContentsMargins(0, 0, 0, 0)
        self._body_scroll = QScrollArea()
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # As needed, not off: the rail takes 52px, and in narrow windows the
        # stage cards' one-line facts are wider than the pane. Clipping them
        # with no way to scroll hid the status pill and the duration.
        self._body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        body = QVBoxLayout(content)
        # The layout pins the content's minimum size, so the content grows by
        # itself when stage cards arrive. Relying on the scroll area's
        # LayoutRequest alone sometimes left it at its old height (cards crushed).
        body.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        body.setContentsMargins(DARK.space_md, DARK.space_md, DARK.space_md, DARK.space_md)
        body.setSpacing(DARK.space_md)
        self._build_body(body)
        self._body_scroll.setWidget(content)
        pane.addWidget(self._body_scroll)
        splitter.addWidget(self._body)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, stretch=1)

        self._current_id: str | None = None
        self._selected_task_id: str | None = None
        self._payload: dict = {}
        self._stages: list[dict] = []
        self._cancellation: dict = {}
        self._cancel_requested = False

    def _build_body(self, layout: QVBoxLayout) -> None:
        # The live card comes first on purpose: while work runs, the question
        # is "where is it", and that answer must not require scrolling.
        self._live = LiveCard()
        layout.addWidget(self._live)

        self._flow_header = SectionHeader("Pipeline")
        layout.addWidget(self._flow_header)
        self._flow = StageFlow()
        self._flow.stage_selected.connect(self._select_stage)
        layout.addWidget(self._flow)

        # Task outcomes and check totals are separate rows: nine blocks in
        # one row overflow the detail pane at the minimum window width and
        # clip the check counts.
        self._tally = Tally()
        layout.addWidget(self._tally)
        self._checks_tally = Tally()
        layout.addWidget(self._checks_tally)

        self._tab_names = ("Tasks", "Runs", "Verification", "Failures")
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_tasks_tab(), "Tasks")
        self._tabs.addTab(self._build_runs_tab(), "Runs")
        self._tabs.addTab(self._build_verification_tab(), "Verification")
        self._tabs.addTab(self._build_failures_tab(), "Failures")
        self._tabs.addTab(self._build_worktree_tab(), "Worktree")
        layout.addWidget(self._tabs)

    def _retitle_tabs(self, tasks: list, runs: list, verifications: list, failures: list) -> None:
        """Show live record counts on the tab labels (e.g. ``Tasks (3)``)."""
        for index, (name, rows) in enumerate(
            zip(self._tab_names, (tasks, runs, verifications, failures), strict=False)
        ):
            self._tabs.setTabText(index, f"{name} ({len(rows)})")

    def _build_tasks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tasks = TablePanel(_TASK_COLUMNS, "No tasks", "This workflow has no tasks yet.")
        self._tasks.selection_changed.connect(self._pick_task)
        self._tasks.activated.connect(self._pick_task)
        layout.addWidget(self._tasks, stretch=1)
        self._task_detail = KeyValueGrid(columns=2)
        layout.addWidget(self._task_detail)
        return page

    def _build_runs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._runs = TablePanel(
            _RUN_COLUMNS, "No agent runs", "Runs appear here as agents execute tasks."
        )
        self._runs.selection_changed.connect(self._pick_run)
        self._runs.activated.connect(self._pick_run)
        layout.addWidget(self._runs, stretch=1)
        self._run_detail = KeyValueGrid(columns=2)
        layout.addWidget(self._run_detail)
        self._run_log = LogText()
        self._run_log.setVisible(False)
        layout.addWidget(self._run_log)
        return page

    def _build_verification_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._verifications = TablePanel(
            _VERIFICATION_COLUMNS,
            "No verification runs",
            "Verification runs when a verification task executes.",
        )
        self._verifications.selection_changed.connect(self._pick_verification)
        self._verifications.activated.connect(self._pick_verification)
        layout.addWidget(self._verifications, stretch=1)
        self._checks = TablePanel(
            _CHECK_COLUMNS, "Select a verification run", "Individual checks appear here."
        )
        layout.addWidget(self._checks, stretch=1)
        return page

    def _build_failures_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._failures = TablePanel(
            _FAILURE_COLUMNS, "No failures", "No failure was recorded for this workflow."
        )
        self._failures.selection_changed.connect(self._pick_failure)
        self._failures.activated.connect(self._pick_failure)
        layout.addWidget(self._failures, stretch=1)
        self._failure_grid = KeyValueGrid(columns=2)
        layout.addWidget(self._failure_grid)
        self._failure_error = LogText()
        self._failure_error.setVisible(False)
        layout.addWidget(self._failure_error)
        return page

    def _build_worktree_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._worktree_grid = KeyValueGrid(columns=2)
        layout.addWidget(self._worktree_grid)
        self._readiness = Tally()
        layout.addWidget(self._readiness)
        actions = QWidget()
        action_row = QVBoxLayout(actions)
        action_row.setContentsMargins(0, 0, 0, 0)
        self._merge = ghost_button("Retry merge")
        self._merge.clicked.connect(self._retry_merge)
        action_row.addWidget(self._merge)
        self._cleanup = ghost_button("Clean up worktree")
        self._cleanup.clicked.connect(self._cleanup_worktree)
        action_row.addWidget(self._cleanup)
        self._open_worktree = ghost_button("Open folder")
        self._open_worktree.clicked.connect(self._open_worktree_folder)
        action_row.addWidget(self._open_worktree)
        layout.addWidget(actions)
        self._artifacts = TablePanel(
            _ARTIFACT_COLUMNS, "No artifacts", "Artifacts appear here as stages produce them."
        )
        layout.addWidget(self._artifacts, stretch=1)
        return page

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------
    # -- list filter and record management -------------------------------
    def sidebar_parts(self) -> tuple[QWidget, ThreadList]:
        """(search + status row, thread list) for the shell's sidebar."""
        return self._sidebar_filters, self._threads

    def _on_thread_selected(self, repository: str, workflow_id: str) -> None:
        if repository != self.repository():
            self.ctx.update_settings({"repository": repository})
        self.show_workflow(workflow_id)

    def _apply_list_filters(self, *_args: object) -> None:
        self._list.set_filter(self._wf_search.text())
        self._list.set_column_filter("status", self._wf_status.currentData() or "")
        self._threads.set_filter(self._wf_search.text(), self._wf_status.currentData() or "")

    def _current_row(self) -> dict | None:
        """Selected list row, falling back to the open workflow."""
        for index in range(self._list.proxy.rowCount()):
            record = self._list.row_of(self._list.proxy.index(index, 0))
            if record and str(record.get("id") or "") == (self._current_id or ""):
                return record
        return None

    def _rename_workflow(self) -> None:
        row = self._current_row()
        if not row:
            self.ctx.toast("No workflow selected", "Pick a workflow to rename it.", "warning")
            return
        workflow_id = str(row.get("id") or "")
        text, accepted = QInputDialog.getText(
            self, "Rename workflow", "Description:", text=str(row.get("description") or "")
        )
        if not accepted:
            return
        repository = self.repository()

        def done(_result: object) -> None:
            self.ctx.toast("Workflow renamed", "", "success")
            self._load_current()
            self.refresh()

        self.submit(
            "rename-workflow",
            lambda: self.ctx.controller.rename_workflow(repository, workflow_id, text),
            done,
            lambda message: self.ctx.toast("Rename failed", message, "error"),
        )

    def _delete_workflow(self) -> None:
        row = self._current_row()
        if not row:
            self.ctx.toast("No workflow selected", "Pick a workflow to delete it.", "warning")
            return
        workflow_id = str(row.get("id") or "")
        counts = self._payload if self._payload.get("workflow", {}).get("id") == workflow_id else {}
        parts = []
        for key, label in (
            ("tasks", "tasks"),
            ("runs", "runs"),
            ("verifications", "verification runs"),
            ("failures", "failures"),
            ("artifacts", "artifacts"),
        ):
            total = len(counts.get(key) or [])
            if total:
                parts.append(f"{total} {label}")
        detail = f"\n\nRemoves {', '.join(parts)}." if parts else ""
        answer = QMessageBox.question(
            self,
            "Delete workflow",
            f"Delete this workflow?\n\n{elide(row.get('description'), 80)}"
            f"{detail}\n\nOn-disk worktrees are left alone.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        repository = self.repository()

        def done(_result: object) -> None:
            self._current_id = None
            self._selected_task_id = None
            self._payload = {}
            self.ctx.toast("Workflow deleted", "", "success")
            self.refresh()

        self.submit(
            "delete-workflow",
            lambda: self.ctx.controller.delete_workflow(repository, workflow_id),
            done,
            lambda message: self.ctx.toast("Delete failed", message, "error"),
        )

    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._list.set_empty_state(
                "No repository selected", "Pick a repository in the sidebar."
            )
            return

        def load() -> object:
            return load_threads(self.ctx.controller, repository)

        def done(data: object) -> None:
            listed = list(data.get("workflows") or []) if isinstance(data, dict) else []
            self._list.set_rows(listed)
            self._threads.set_repository_data(repository, data)
            if self._current_id is None and listed:
                self.show_workflow(str(listed[0].get("id") or ""))
            elif self._current_id:
                self._load_current()

        self.submit(
            "workflows", load, done, lambda message: self.ctx.toast("Load failed", message, "error")
        )

    def show_workflow(self, workflow_id: str, task_id: str | None = None) -> None:
        """Open one workflow, keeping the selection across refreshes.

        ``task_id`` is the deep-link half: the shell passes a task when it
        navigates here from a run or a notification.
        """
        workflow_id = str(workflow_id or "")
        if not workflow_id:
            return
        self._current_id = workflow_id
        self._cancel_requested = False
        self._threads.set_current(self.repository(), workflow_id)
        self._select_by_id(self._list, workflow_id)
        self._load_current(task_id)

    def _load_current(self, task_id: str | None = None) -> None:
        workflow_id = self._current_id
        repository = self.repository()
        if not workflow_id or not repository:
            return
        if task_id:
            self._selected_task_id = str(task_id)

        def load() -> object:
            controller = self.ctx.controller
            payload = controller.get_workflow(repository, workflow_id)
            artifacts = controller.list_artifacts(repository, workflow_id=workflow_id)
            events = controller.query_events(repository, workflow_id=workflow_id, limit=30)
            try:
                readiness = controller.workflow_readiness(repository, workflow_id)
            except AttributeError:
                # Without the readiness read the merge state shows as blocked
                # rather than being invented ready.
                readiness = {"ready": False, "reasons": ["readiness unavailable"]}
            return {
                "payload": payload if isinstance(payload, dict) else {},
                "artifacts": list(artifacts or []),
                "events": list(events or []),
                "readiness": readiness,
            }

        self.submit("current", load, self._on_payload)

    def _on_payload(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

        def records(key: str) -> list:
            value = payload.get(key)
            return (
                [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
            )

        workflow = records("workflow")[0] if records("workflow") else {}
        tasks, runs = records("tasks"), records("runs")
        verifications, failures = records("verifications"), records("failures")
        artifacts = [row for row in (data.get("artifacts") or []) if isinstance(row, dict)]
        self._payload = {
            "workflow": workflow,
            "tasks": tasks,
            "runs": runs,
            "verifications": verifications,
            "failures": failures,
            "artifacts": artifacts,
            "worktree_ref": payload.get("worktree_ref")
            if isinstance(payload.get("worktree_ref"), dict)
            else {},
        }

        live = projection.build_live_panel(
            workflow, tasks, runs, verifications, failures, events=data.get("events")
        )
        self._apply_state(
            projection.cancellation_state(
                live, self._cancel_requested, self.ctx.operation_active()
            ),
            workflow,
        )
        self._live.set_live(live)
        self._stages = list(live.get("stages") or [])
        self._flow.set_stages(self._stages)
        self._flow.attach_clicks()
        self._flow.highlight(str(live.get("stage_key") or ""))
        self._apply_tally(tasks, runs, verifications, failures)

        self._tasks.set_rows(tasks)
        self._runs.set_rows(runs)
        self._verifications.set_rows(verifications)
        self._failures.set_rows(failures)
        self._artifacts.set_rows(artifacts)
        self._retitle_tabs(tasks, runs, verifications, failures)
        self._apply_worktree(data.get("readiness"))
        self._select_by_id(self._tasks, self._selected_task_id or "")

    def _apply_tally(self, tasks: list, runs: list, verifications: list, failures: list) -> None:
        counts: dict[str, int] = {}
        for row in tasks:
            key = str(row.get("status") or "pending")
            counts[key] = counts.get(key, 0) + 1
        verification_totals = projection.verification_totals(verifications)
        self._tally.set_items(
            [
                ("Tasks", len(tasks), "active"),
                ("Passed", counts.get("passed", 0), "passed"),
                ("Running", counts.get("running", 0), "running"),
                ("Failed", counts.get("failed", 0) + counts.get("blocked", 0), "failed"),
                ("Runs", len(runs), "active"),
                ("Failures", len(failures), "failed" if failures else "pending"),
            ]
        )
        self._checks_tally.set_items(
            [
                ("Checks passed", int(verification_totals["passed"]), "passed"),
                ("Checks failed", int(verification_totals["failed"]), "failed"),
                ("Checks skipped", int(verification_totals["skipped"]), "skipped"),
            ]
        )

    def _apply_state(self, state: dict, workflow: dict) -> None:
        """Reflect run/cancel state everywhere it changes what is possible.

        Cancellation has to be visible the moment it is asked for, and the
        actions that would fight the running operation must be off until the
        run is no longer live.
        """
        status = str(workflow.get("status") or "pending")
        self._cancellation = state
        self._status_badge.set_status(state.get("state") or status)
        self._cancel.setVisible(
            bool(state.get("cancellable")) or bool(state.get("cancel_requested"))
        )
        self._cancel.setEnabled(bool(state.get("cancellable")))
        self._cancel.setText("Cancelling" if state.get("cancel_requested") else "Cancel run")
        self._recover.setVisible(not state.get("running"))

    def _apply_worktree(self, readiness: object) -> None:
        worktree = projection.worktree_summary(
            (self._payload.get("worktree_ref") or {}),
            self._payload.get("runs"),
            readiness,
        )
        merge = dict(worktree.get("readiness") or {})
        self._worktree_grid.clear()
        self._worktree_grid.add_row("Branch", worktree.get("branch") or "none")
        self._worktree_grid.add_row("Base branch", worktree.get("base_branch"))
        self._worktree_grid.add_row(
            "Base commit", short_id(worktree.get("base_commit"), 10), mono_value=True
        )
        self._worktree_grid.add_row("Path", worktree.get("path") or "none", mono_value=True)
        self._worktree_grid.add_row(
            "Changed files",
            f"{worktree.get('changed_count', 0)}"
            + ("  (working tree dirty)" if worktree.get("dirty") else ""),
        )
        self._worktree_grid.add_row(
            "Merge readiness",
            str(merge.get("label") or "unknown"),
            mono_value=bool(merge.get("ready")),
        )
        reasons = merge.get("reasons") or []
        self._worktree_grid.add_row(
            "Blocked by", "; ".join(str(reason) for reason in reasons) or "nothing"
        )
        self._worktree_grid.add_row("Diff", worktree.get("diff_stat"), mono_value=True)
        self._readiness.set_items(
            [
                (
                    "Ready to merge",
                    1 if merge.get("ready") else 0,
                    str(merge.get("state") or "blocked"),
                ),
                ("Changed files", int(worktree.get("changed_count") or 0), "active"),
                ("Blocking reasons", len(reasons), "failed" if reasons else "passed"),
            ]
        )
        # A worktree must not be merged or cleaned out from under a live run, so
        # those actions wait on the recorded cancellation state, not on whether
        # a button happens to be enabled at this instant.
        running = bool(self._cancellation.get("running"))
        present = bool(worktree.get("present"))
        self._merge.setEnabled(present and not running)
        self._cleanup.setEnabled(present and not running)
        self._open_worktree.setEnabled(present)

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    @staticmethod
    def _select_by_id(table: TablePanel, item_id: str) -> None:
        """Re-select a row after a reload so focus survives a refresh."""
        if not item_id:
            return
        proxy = table.proxy
        for index in range(proxy.rowCount()):
            record = table.row_of(proxy.index(index, 0))
            if record and str(record.get("id") or "") == item_id:
                table.select_row_index(index)
                return

    def _pick_workflow(self, row: object) -> None:
        if isinstance(row, dict):
            workflow_id = str(row.get("id") or "")
            if workflow_id and workflow_id != self._current_id:
                self.show_workflow(workflow_id)

    def _select_stage(self, stage: object) -> None:
        """A stage is a way to reach its task; selection is what follows."""
        if not isinstance(stage, dict):
            return
        self._flow.highlight(str(stage.get("key") or ""))
        task_ids = [str(item) for item in stage.get("task_ids") or () if item]
        if task_ids:
            self._tabs.setCurrentIndex(0)
            self._select_by_id(self._tasks, task_ids[0])

    def _pick_task(self, row: object) -> None:
        detail = projection.task_detail(
            row if isinstance(row, dict) else {},
            self._payload.get("runs"),
            self._payload.get("verifications"),
            self._payload.get("failures"),
            self._payload.get("artifacts"),
        )
        self._selected_task_id = detail.get("id") or None
        self._task_detail.clear()
        self._task_detail.add_row("Description", detail.get("description") or "-")
        self._task_detail.add_row("Role", detail.get("role"))
        self._task_detail.add_row("Agent", detail.get("agent") or "unassigned")
        self._task_detail.add_row("Status", detail.get("status"))
        self._task_detail.add_row("Attempts", detail.get("attempts"))
        self._task_detail.add_row("Dependencies", ", ".join(detail["dependencies"]) or "none")
        self._task_detail.add_row(
            "Verification",
            f"{detail.get('verification')}  (verified: "
            f"{'yes' if detail.get('verified') else 'no'})",
        )
        self._task_detail.add_row("Created", format_timestamp(detail.get("created_at")))
        self._task_detail.add_row("Started", format_timestamp(detail.get("started_at")))
        self._task_detail.add_row("Finished", format_timestamp(detail.get("finished_at")))
        self._task_detail.add_row(
            "Related runs",
            f"{len(detail['runs'])}",
        )
        self._task_detail.add_row("Related verifications", f"{len(detail['verifications'])}")
        self._task_detail.add_row("Related failures", f"{len(detail['failures'])}")
        self._task_detail.add_row("Related artifacts", f"{len(detail['artifacts'])}")
        self._task_detail.add_row("Result", elide(detail.get("result"), 400), mono_value=True)

    def _pick_run(self, row: object) -> None:
        detail = projection.run_detail(row if isinstance(row, dict) else {})
        self._run_detail.clear()
        self._run_detail.add_row("Agent", detail.get("agent") or "-")
        self._run_detail.add_row("Model", detail.get("model") or "-")
        self._run_detail.add_row("Role", detail.get("role"))
        self._run_detail.add_row("Status", detail.get("status"))
        self._run_detail.add_row("Attempt", detail.get("attempt"))
        self._run_detail.add_row("Exit code", detail.get("exit_code"))
        self._run_detail.add_row("Duration", detail.get("duration_text"))
        self._run_detail.add_row("Started", format_timestamp(detail.get("started_at")))
        self._run_detail.add_row("Ended", format_timestamp(detail.get("ended_at")))
        self._run_detail.add_row("Worktree", detail.get("worktree"), mono_value=True)
        self._run_detail.add_row("Relationship", detail.get("relationship"))
        self._run_detail.add_row("Changed files", ", ".join(detail["files_changed"]) or "none")
        self._run_detail.add_row("Diff", detail.get("diff_stat"), mono_value=True)
        self._run_detail.add_row("Error", elide(detail.get("error"), 200))
        self._run_detail.add_row(
            "Structured result",
            "present" if detail.get("structured_result") else "none",
        )
        self._run_log.set_log(self._structured_text(detail.get("structured_result")))
        self._run_log.setVisible(bool(detail.get("structured_result")))
        if not isinstance(row, dict):
            self.ctx.open_run(str(detail.get("id") or ""))

    @staticmethod
    def _structured_text(value: object) -> str:
        """Render a structured result as readable text, never as repr noise."""
        import json

        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(value)

    def _pick_verification(self, row: object) -> None:
        if not isinstance(row, dict):
            self._checks.set_rows([])
            self._checks.set_empty_state(
                "Select a verification run", "Individual checks appear here."
            )
            return
        run_id = str(row.get("id") or "")
        repository = self.repository()

        def load() -> object:
            return self.ctx.controller.get_verification_run(repository, run_id)

        def done(detail: object) -> None:
            checks = detail.get("checks") if isinstance(detail, dict) else None
            rows = [dict(item) for item in (checks or []) if isinstance(item, dict)]
            self._checks.set_rows(rows)
            totals = projection.verification_totals([], rows)
            self._checks.set_empty_state(
                str(totals["summary"]) if rows else "No checks recorded",
                "" if rows else "This verification run recorded no individual checks.",
            )

        self.submit(
            "checks",
            load,
            done,
            lambda message: self.ctx.toast("Checks unavailable", message, "error"),
        )

    def _pick_failure(self, row: object) -> None:
        detail = projection.failure_summary(
            row if isinstance(row, dict) else {},
            self._payload.get("runs"),
            self._payload.get("tasks"),
        )
        self._failure_grid.clear()
        self._failure_grid.add_row("Category", detail.get("category"))
        self._failure_grid.add_row("Severity", detail.get("severity"))
        self._failure_grid.add_row("Recommended action", detail.get("action"))
        self._failure_grid.add_row("Retryable", "yes" if detail.get("retryable") else "no")
        self._failure_grid.add_row("Repairable", "yes" if detail.get("repairable") else "no")
        self._failure_grid.add_row("Recovery", detail.get("recovery_state") or "none")
        self._failure_grid.add_row("Task", detail.get("task_label"))
        self._failure_grid.add_row("Run", detail.get("run_label") or "none")
        self._failure_grid.add_row("Created", format_timestamp(detail.get("created_at")))
        evidence = "\n".join(
            part
            for part in (str(detail.get("error") or ""), str(detail.get("evidence") or ""))
            if part
        )
        self._failure_error.set_log(evidence)
        self._failure_error.setVisible(bool(evidence))

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------
    def _cancel_operation(self) -> None:
        """Ask the controller to stop, then show that immediately.

        The button locks and the badge reads "cancelling" before any round
        trip, so the surface never implies the run is still proceeding
        normally after a cancel was accepted.
        """
        try:
            self.ctx.controller.cancel()
        except Exception as error:  # noqa: BLE001 - surface a failed cancel
            self.ctx.toast("Cancel failed", str(error), "error")
            return
        self._cancel_requested = True
        self._cancel.setEnabled(False)
        self._cancel.setText("Cancelling")
        self._status_badge.set_status("cancelling")
        self.ctx.toast(
            "Cancellation requested", "The running operation will stop shortly.", "warning"
        )
        self.refresh()

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
        workflow_id = self._current_id

        def done(result: object) -> None:
            if isinstance(result, dict) and result:
                counts = ", ".join(f"{key}: {value}" for key, value in result.items())
            else:
                counts = "nothing to recover"
            self.ctx.toast("Recovery applied", counts, "success")
            self.refresh()

        self.submit(
            "recover",
            lambda: self.ctx.controller.recover_interrupted(repository, workflow_id),
            done,
        )

    def _worktree_path(self) -> str:
        return str((self._payload.get("worktree_ref") or {}).get("path") or "")

    def _retry_merge(self) -> None:
        path = self._worktree_path()
        if not path:
            return
        if not _confirm(
            "Retry merge",
            f"Merge this managed worktree branch into the current base branch now?\n\n{path}",
        ):
            return
        repository = self.repository()

        def done(result: object) -> None:
            payload = result if isinstance(result, dict) else {}
            self.ctx.toast(
                "Merged", f"{payload.get('branch')} -> {payload.get('base_branch')}", "success"
            )
            self.refresh()

        self.submit("merge", lambda: self.ctx.controller.retry_merge(repository, path), done)

    def _cleanup_worktree(self) -> None:
        path = self._worktree_path()
        if not path:
            return
        if not _confirm(
            "Clean up worktree",
            f"Remove this clean worktree? An unmerged branch will be preserved.\n\n{path}",
            danger=True,
        ):
            return
        repository = self.repository()

        def done(result: object) -> None:
            payload = result if isinstance(result, dict) else {}
            detail = (
                "Its branch was deleted."
                if payload.get("deleted_branch")
                else "The branch was preserved."
            )
            self.ctx.toast("Worktree removed", detail, "success")
            self.refresh()

        self.submit("cleanup", lambda: self.ctx.controller.cleanup_worktree(repository, path), done)

    def _open_worktree_folder(self) -> None:
        path = self._worktree_path()
        if not path:
            return
        try:
            open_path_external(path)
        except OSError as error:
            self.ctx.toast("Unable to open folder", str(error), "error")

    def on_operation_event(self, event: dict[str, object]) -> None:
        kind = str(event.get("kind") or "")
        if kind not in (
            "workflow-started",
            "workflow-result",
            "result",
            "thread-finished",
            "cancelled",
            "error",
            "conflict",
        ):
            return
        if kind in ("thread-finished", "workflow-result"):
            self._cancel_requested = False
        workflow_id = str(event.get("workflow_id") or "")
        if workflow_id and workflow_id != self._current_id:
            self.show_workflow(workflow_id)
        else:
            self.refresh()
