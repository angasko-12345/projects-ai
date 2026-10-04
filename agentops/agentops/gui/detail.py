"""Shared detail panels for runs, tasks, verification, failures, worktrees,
artifacts, and logs.

Each panel is a fixed widget tree filled from controller payloads (no widget
churn), with reads submitted through the bridge. Panels never talk to SQLite,
git, or subprocesses directly - worktree/log/artifact actions call the
controller, mutations emit ``changed`` so the owning view re-lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .context import AsyncMixin, ViewContext
from .format import elide, format_duration, format_timestamp, format_size, short_id
from .platform import open_path_external
from .tokens import DARK
from .widgets import (
    Badge,
    CollapsibleSection,
    EmptyState,
    KeyValueGrid,
    LogText,
    TablePanel,
    TableColumn,
    faint,
)


class DetailPanel(QWidget, AsyncMixin):
    """Header + scrollable body + empty placeholder shared by all panels."""

    changed = Signal()  # underlying data mutated (merge, cleanup, ...)

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)

        self._stack = QStackedWidget(self)
        self._placeholder = EmptyState("Nothing selected",
                                       "Pick an item to see its details.")
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(DARK.space_md)

        header = QHBoxLayout()
        header.setSpacing(DARK.space_sm)
        self._title = QLabel("-")
        self._title.setProperty("role", "title")
        header.addWidget(self._title)
        self._subtitle = QLabel("")
        self._subtitle.setProperty("role", "subtitle")
        header.addWidget(self._subtitle)
        self._badge = Badge()
        self._badge.hide()
        header.addWidget(self._badge)
        header.addStretch(1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(DARK.space_sm)
        header.addLayout(self._actions)
        content_layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._body_widget = QWidget()
        self._body_layout = QVBoxLayout(self._body_widget)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(DARK.space_md)
        self._scroll.setWidget(self._body_widget)
        content_layout.addWidget(self._scroll, stretch=1)

        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self._build_body()

    # -- building blocks ---------------------------------------------
    def _build_body(self) -> None:
        """Subclasses add their fixed widgets here."""

    def add_grid(self, columns: int = 2) -> KeyValueGrid:
        grid = KeyValueGrid(columns=columns)
        self._body_layout.addWidget(grid)
        return grid

    def add_widget(self, widget: QWidget) -> QWidget:
        self._body_layout.addWidget(widget)
        return widget

    def add_section(self, title: str, widget: QWidget,
                    expanded: bool = True) -> CollapsibleSection:
        section = CollapsibleSection(title)
        section.add(widget)
        self._body_layout.addWidget(section)
        section.set_expanded(expanded, animate=False)
        return section

    def add_header_action(self, text: str, callback: Callable[[], None],
                          variant: str = "") -> QPushButton:
        button = QPushButton(text)
        if variant:
            button.setProperty("variant", variant)
        button.clicked.connect(callback)
        self._actions.addWidget(button)
        return button

    # -- state --------------------------------------------------------
    def set_heading(self, title: str, status: object | None = None,
                    subtitle: str = "") -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        if status is None:
            self._badge.hide()
        else:
            self._badge.set_status(status)
            self._badge.show()

    def show_placeholder(self, heading: str, message: str = "") -> None:
        self._placeholder.set_state(heading, message)
        self._stack.setCurrentWidget(self._placeholder)

    def show_content(self) -> None:
        self._stack.setCurrentWidget(self._content)

    def repository(self) -> str:
        return str(self.ctx.repository()).strip()


# ---------------------------------------------------------------------------
# run detail
# ---------------------------------------------------------------------------

class RunDetailPanel(DetailPanel):
    """One persisted agent run: metadata, error, files changed, log."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._error_text = LogText()
        self._error_section = self.add_section("Error", self._error_text, expanded=False)
        self._files_text = LogText()
        self._files_section = self.add_section("Changed files", self._files_text, expanded=False)
        self._log_text = LogText()
        self._log_section = self.add_section("Log", self._log_text, expanded=False)
        self.add_header_action("Open log folder", self._open_log_folder)
        self.add_header_action("Copy run id", self._copy_run_id, variant="ghost")
        self._run: dict | None = None

    def set_run(self, run: dict | None) -> None:
        self._run = run
        if not run:
            self.show_placeholder("No run selected", "Pick a run to inspect it.")
            return
        self.set_heading(f"Run {short_id(run.get('id'), 8)}", run.get("status"))
        self._grid.clear()
        self._grid.add_row("Agent", run.get("agent") or "-")
        self._grid.add_row("Role", run.get("role"))
        self._grid.add_row("Model", run.get("model"))
        self._grid.add_row("Attempt", run.get("attempt"))
        self._grid.add_row("Exit code", run.get("exit_code"))
        self._grid.add_row("Duration", format_duration(run.get("duration_seconds")))
        self._grid.add_row("Started", format_timestamp(run.get("started_at")))
        self._grid.add_row("Ended", format_timestamp(run.get("ended_at")))
        self._grid.add_row("Worktree", run.get("worktree"))
        self._grid.add_row("Working directory", run.get("working_directory"), mono_value=True)
        self._grid.add_row("Relationship", run.get("relationship"))
        self._grid.add_row("Failure classification", run.get("failure_classification"))
        error = str(run.get("error") or "").strip()
        self._error_section.setVisible(bool(error))
        self._error_text.set_log(error)
        diff_stat = str(run.get("diff_stat") or "").strip()
        files = run.get("files_changed") or []
        files_text = diff_stat or "\n".join(str(item) for item in files)
        self._files_section.setVisible(bool(files_text.strip()))
        self._files_text.set_log(files_text)
        self._log_text.set_log("")
        self._log_section.setVisible(False)
        self.show_content()
        self._load_log()

    def _load_log(self) -> None:
        run = self._run
        if not run or not run.get("task_id"):
            return
        task_id = str(run["task_id"])
        log_path = str(run.get("log_path") or "")

        def load() -> object:
            controller = self.ctx.controller
            entries = controller.list_logs(self.repository(), task_id=task_id)
            match = None
            for entry in entries:
                if log_path and str(entry.get("path", "")) == log_path:
                    match = entry
                    break
            if match is None:
                return None
            return controller.read_log(self.repository(), str(match["name"]))

        self.submit("log", load, self._on_log)

    def _on_log(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        text = str(data.get("text", "")).rstrip()
        if not text:
            return
        if data.get("truncated"):
            text = "(showing the most recent part of this log)\n" + text
        self._log_text.set_log(text)
        self._log_section.setVisible(True)

    def _open_log_folder(self) -> None:
        run = self._run or {}
        log_path = str(run.get("log_path") or "")
        if not log_path:
            return
        try:
            open_path_external(Path(log_path).parent)
        except OSError as error:
            self.ctx.toast("Unable to open folder", str(error), "error")

    def _copy_run_id(self) -> None:
        run_id = str((self._run or {}).get("id") or "")
        if run_id:
            QApplication_clipboard().setText(run_id)
            self.ctx.toast("Copied", "Run id copied to the clipboard.", "info")


def QApplication_clipboard():  # noqa: N802 - late import avoids Qt at module import
    from PySide6.QtWidgets import QApplication

    return QApplication.clipboard()


# ---------------------------------------------------------------------------
# task detail
# ---------------------------------------------------------------------------

_TASK_RUN_COLUMNS = (
    TableColumn("status", "Status", 110),
    TableColumn("agent", "Agent", 120),
    TableColumn("attempt", "Attempt", 80, format=lambda v: "-" if v is None else str(v)),
    TableColumn("exit_code", "Exit", 70),
    TableColumn("duration_seconds", "Duration", 90,
                format=format_duration, sort=lambda v: float(v) if isinstance(v, (int, float)) else -1.0),
    TableColumn("model", "Model", 130),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
    TableColumn("id", "Run", 90, format=lambda v: short_id(v, 8)),
)

_TASK_VERIFICATION_COLUMNS = (
    TableColumn("overall_status", "Status", 110),
    TableColumn("profile_name", "Profile", 130),
    TableColumn("passed_checks", "Passed", 80),
    TableColumn("total_checks", "Checks", 80),
    TableColumn("failed_checks", "Failed", 80),
    TableColumn("duration_seconds", "Duration", 90,
                format=format_duration, sort=lambda v: float(v) if isinstance(v, (int, float)) else -1.0),
    TableColumn("started_at", "Started", 150, format=format_timestamp),
)

_TASK_FAILURE_COLUMNS = (
    TableColumn("category", "Category", 170),
    TableColumn("severity", "Severity", 100),
    TableColumn("recommended_action", "Action", 160),
    TableColumn("retryable", "Retryable", 90,
                format=lambda v: "yes" if v else "no"),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
)


class TaskDetailPanel(DetailPanel):
    """One task: metadata, result, and its runs/verifications/failures."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._description = QLabel("")
        self._description.setWordWrap(True)
        self._description.setProperty("role", "muted")
        self._body_layout.addWidget(self._description)
        self._result_text = LogText()
        self._result_section = self.add_section("Result", self._result_text, expanded=False)

        self._runs_table = TablePanel(
            _TASK_RUN_COLUMNS, "No agent runs", "Runs for this task will appear here."
        )
        self._runs_section = self.add_section("Agent runs", self._runs_table)
        self._runs_table.activated.connect(
            lambda row: self.ctx.open_run(str(row.get("id", "")))
        )

        self._verification_table = TablePanel(
            _TASK_VERIFICATION_COLUMNS, "No verification runs",
            "Verification runs for this task will appear here."
        )
        self._verification_section = self.add_section("Verification", self._verification_table)

        self._failures_table = TablePanel(
            _TASK_FAILURE_COLUMNS, "No failures", "No failures were recorded for this task."
        )
        self._failures_section = self.add_section("Failures", self._failures_table)
        self._task: dict | None = None

    def set_task(self, task: dict | None) -> None:
        self._task = task
        if not task:
            self.show_placeholder("No task selected", "Pick a task to inspect it.")
            return
        status = task.get("status")
        self.set_heading(f"Task {short_id(task.get('id'), 8)}", status,
                         subtitle=str(task.get("role") or ""))
        self._grid.clear()
        self._grid.add_row("Id", task.get("id"), mono_value=True)
        self._grid.add_row("Role", task.get("role"))
        self._grid.add_row("Assigned agent", task.get("assigned_agent") or "-")
        self._grid.add_row("Attempts", f"{task.get('attempts')} / {task.get('max_attempts')}")
        self._grid.add_row("Verified", "yes" if task.get("verified") else "no")
        self._grid.add_row("Dependencies", ", ".join(task.get("dependencies") or ()) or "-")
        self._grid.add_row("Created", format_timestamp(task.get("created_at")))
        self._grid.add_row("Started", format_timestamp(task.get("started_at")))
        self._grid.add_row("Finished", format_timestamp(task.get("finished_at")))
        description = str(task.get("description") or "")
        self._description.setText(description)
        self._description.setVisible(bool(description))
        result = str(task.get("result") or "").rstrip()
        self._result_section.setVisible(bool(result))
        self._result_text.set_log(result)
        self._runs_table.set_rows([])
        self._verification_table.set_rows([])
        self._failures_table.set_rows([])
        self.show_content()
        self._load_related()

    def _load_related(self) -> None:
        task = self._task
        if not task:
            return
        task_id = str(task.get("id") or "")
        if not task_id:
            return
        controller = self.ctx.controller
        repository = self.repository()
        self.submit(
            "runs",
            lambda: controller.list_agent_runs(repository, task_id=task_id),
            lambda rows: self._runs_table.set_rows(list(rows or [])),
        )
        self.submit(
            "verifications",
            lambda: controller.list_verification_runs(repository, task_id=task_id),
            lambda rows: self._verification_table.set_rows(list(rows or [])),
        )
        self.submit(
            "failures",
            lambda: controller.list_failures(repository, task_id=task_id),
            lambda rows: self._failures_table.set_rows(list(rows or [])),
        )


# ---------------------------------------------------------------------------
# verification detail
# ---------------------------------------------------------------------------

_CHECK_COLUMNS = (
    TableColumn("name", "Check", 170),
    TableColumn("check_class", "Class", 110),
    TableColumn("status", "Status", 100),
    TableColumn("required", "Required", 90,
                format=lambda v: "yes" if v else "no"),
    TableColumn("exit_code", "Exit", 70),
    TableColumn("duration_seconds", "Duration", 90,
                format=format_duration, sort=lambda v: float(v) if isinstance(v, (int, float)) else -1.0),
    TableColumn("failure_reason", "Failure reason", 260,
                format=lambda v: elide(v, 90)),
)


class VerificationDetailPanel(DetailPanel):
    """One verification run: check list, report totals, transcript."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._checks = TablePanel(
            _CHECK_COLUMNS, "Loading checks...", "Verification checks will appear here."
        )
        self._checks_section = self.add_section("Checks", self._checks)
        self._report_text = LogText()
        self._report_section = self.add_section("Report", self._report_text, expanded=False)
        self._run: dict | None = None

    def set_verification(self, summary: dict | None) -> None:
        """Fill from a list payload, then load checks/report in the background."""
        self._run = summary
        if not summary:
            self.show_placeholder("No verification selected",
                                  "Pick a verification run to inspect it.")
            return
        run_id = str(summary.get("id") or "")
        self.set_heading(f"Verification {short_id(run_id, 8)}",
                         summary.get("overall_status") or summary.get("status"),
                         subtitle=str(summary.get("profile_name") or ""))
        self._fill_grid(summary)
        self._checks.set_empty_state("Loading checks...", "")
        self._checks.set_rows([])
        self._report_section.setVisible(False)
        self.show_content()
        if run_id:
            controller = self.ctx.controller
            self.submit(
                "detail",
                lambda: controller.get_verification_run(self.repository(), run_id),
                self._on_detail,
                lambda _message: self._checks.set_empty_state(
                    "Details unavailable", "Could not load the check list."
                ),
            )

    def set_detail(self, detail: dict | None) -> None:
        """Fill directly from a ``get_verification_run`` payload (no re-fetch)."""
        run = detail.get("run") if isinstance(detail, dict) else None
        if not isinstance(run, dict):
            self.set_verification(None)
            return
        self._run = run
        self.set_heading(
            f"Verification {short_id(run.get('id'), 8)}",
            run.get("overall_status") or run.get("status"),
            subtitle=str(run.get("profile_name") or ""),
        )
        self._checks.set_rows(list(detail.get("checks") or []))
        self.show_content()
        self._apply_detail(detail)

    def _on_detail(self, detail: object) -> None:
        if not isinstance(detail, dict):
            return
        if isinstance(detail.get("run"), dict):
            run = detail["run"]
            self.set_heading(
                f"Verification {short_id(run.get('id'), 8)}",
                run.get("overall_status") or run.get("status"),
                subtitle=str(run.get("profile_name") or ""),
            )
        self._checks.set_rows(list(detail.get("checks") or []))
        self._apply_detail(detail)

    def _apply_detail(self, detail: dict) -> None:
        run = detail.get("run") if isinstance(detail.get("run"), dict) else {}
        # list payload first, then record fields (record wins where both exist)
        merged = dict(self._run or {})
        merged.update(run)
        self._fill_grid(merged)
        report = detail.get("report")
        if isinstance(report, dict):
            self._grid.add_row("Report status", report.get("overall_status"))
        else:
            report = None
        transcript = str((report or {}).get("transcript") or "").rstrip()
        self._report_section.setVisible(bool(transcript))
        self._report_text.set_log(transcript)

    def _fill_grid(self, run: dict) -> None:
        self._grid.clear()
        self._grid.add_row("Profile", run.get("profile_name"))
        self._grid.add_row("Status", run.get("overall_status") or run.get("status"))
        self._grid.add_row("Mode", run.get("mode"))
        self._grid.add_row("Total checks", run.get("total_checks"))
        self._grid.add_row("Passed", run.get("passed_checks"))
        self._grid.add_row("Failed", run.get("failed_checks"))
        self._grid.add_row("Skipped", run.get("skipped_checks"))
        self._grid.add_row("Required failures", run.get("required_failures"))
        self._grid.add_row("Duration", format_duration(run.get("duration_seconds")))
        self._grid.add_row("Started", format_timestamp(run.get("started_at")))
        self._grid.add_row("Ended", format_timestamp(run.get("ended_at")))


# ---------------------------------------------------------------------------
# failure detail
# ---------------------------------------------------------------------------

class FailureDetailPanel(DetailPanel):
    """One failure record: classification, evidence, recovery information."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._primary_text = LogText()
        self._primary_section = self.add_section("Primary error", self._primary_text,
                                                 expanded=False)
        self._evidence_text = LogText()
        self._evidence_section = self.add_section("Evidence", self._evidence_text,
                                                  expanded=False)
        self._failure: dict | None = None

    def set_failure(self, failure: dict | None) -> None:
        self._failure = failure
        if not failure:
            self.show_placeholder("No failure selected",
                                  "Pick a failure to see classification and recovery details.")
            return
        category = str(failure.get("category") or "unknown")
        self.set_heading(category.replace("_", " ").title(),
                         failure.get("severity") or "unknown",
                         subtitle=str(failure.get("source") or ""))
        self._grid.clear()
        self._grid.add_row("Recommended action", failure.get("recommended_action"))
        self._grid.add_row("Retryable", failure.get("retryable"))
        self._grid.add_row("Repairable", failure.get("repairable"))
        self._grid.add_row("Recovery state", failure.get("recovery_state") or "-")
        self._grid.add_row("Attempt", failure.get("attempt"))
        self._grid.add_row("Repair cycle", failure.get("repair_cycle"))
        self._grid.add_row("Workflow", short_id(failure.get("workflow_id"), 8))
        self._grid.add_row("Task", short_id(failure.get("task_id"), 8))
        self._grid.add_row("Agent run", short_id(failure.get("agent_run_id"), 8))
        self._grid.add_row("Verification run", short_id(failure.get("verification_run_id"), 8))
        self._grid.add_row("Created", format_timestamp(failure.get("created_at")))
        self._grid.add_row("Updated", format_timestamp(failure.get("updated_at")))
        primary = str(failure.get("primary_error") or "").strip()
        self._primary_section.setVisible(bool(primary))
        self._primary_text.set_log(primary)
        evidence = failure.get("evidence")
        structured = failure.get("structured_evidence")
        evidence_text = ""
        if structured:
            try:
                evidence_text = json.dumps(structured, indent=2, sort_keys=True)
            except (TypeError, ValueError):
                evidence_text = str(structured)
        elif evidence:
            evidence_text = str(evidence)
        self._evidence_section.setVisible(bool(evidence_text))
        self._evidence_text.set_log(evidence_text)
        self.show_content()


# ---------------------------------------------------------------------------
# worktree detail
# ---------------------------------------------------------------------------

class WorktreeDetailPanel(DetailPanel):
    """One Git worktree: provenance, changed files, merge/cleanup actions."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._status_badge_row = QLabel("")
        self._status_badge_row.setProperty("role", "muted")
        self._body_layout.addWidget(self._status_badge_row)
        self._inspect_text = LogText()
        self._inspect_section = self.add_section("Changed files", self._inspect_text,
                                                 expanded=False)
        self.add_header_action("Retry merge", self._retry_merge, variant="primary")
        self.add_header_action("Clean up...", self._cleanup, variant="danger")
        self.add_header_action("Open folder", self._open_folder, variant="ghost")
        self._entry: dict | None = None
        self._delete_branch = False

    def set_worktree(self, entry: dict | None, delete_unmerged_branch: bool = False) -> None:
        self._entry = entry
        self._delete_branch = delete_unmerged_branch
        if not entry:
            self.show_placeholder("No worktree selected",
                                  "Pick a worktree to inspect provenance and changes.")
            return
        path = str(entry.get("path") or "")
        status = str(entry.get("status") or "").strip()
        state = "clean" if not status else ("unavailable" if status == "(status unavailable)" else "dirty")
        self.set_heading(Path(path).name or path, state,
                         subtitle="managed" if entry.get("managed") else "unmanaged")
        self._grid.clear()
        self._grid.add_row("Path", path, mono_value=True)
        self._grid.add_row("Branch", entry.get("branch"))
        self._grid.add_row("Base branch", entry.get("base_branch"))
        self._grid.add_row("Base commit", short_id(entry.get("base_commit"), 10), mono_value=True)
        self._grid.add_row("Head", short_id(entry.get("head"), 10), mono_value=True)
        self._grid.add_row("Working tree", status or "clean", mono_value=bool(status))
        self._inspect_text.set_log("")
        self._inspect_section.setVisible(False)
        self.show_content()
        self._inspect()

    def _inspect(self) -> None:
        entry = self._entry
        if not entry:
            return
        controller = self.ctx.controller
        repository = self.repository()
        path = str(entry.get("path") or "")
        self.submit(
            "inspect",
            lambda: controller.inspect_worktree(repository, path),
            self._on_inspect,
        )

    def _on_inspect(self, info: object) -> None:
        if not isinstance(info, dict):
            return
        diff_stat = str(info.get("diff_stat") or "").strip()
        self._inspect_section.setVisible(bool(diff_stat))
        self._inspect_text.set_log(diff_stat)
        status = str(info.get("status") or "").strip()
        self._status_badge_row.setText(f"Working tree: {status or 'clean'}")

    def _retry_merge(self) -> None:
        entry = self._entry
        if not entry:
            return
        path = str(entry.get("path") or "")
        if not _confirm("Retry merge",
                        "Merge this managed worktree branch into the current base "
                        f"branch now?\n\n{path}", danger=False):
            return
        controller = self.ctx.controller
        repository = self.repository()

        def merge() -> object:
            return controller.retry_merge(repository, path)

        def done(result: object) -> None:
            payload = result if isinstance(result, dict) else {}
            self.ctx.toast("Merged", f"{payload.get('branch')} -> {payload.get('base_branch')}",
                           "success")
            self.changed.emit()

        self.submit("retry_merge", merge, done)

    def _cleanup(self) -> None:
        entry = self._entry
        if not entry:
            return
        path = str(entry.get("path") or "")
        if self._delete_branch:
            question = ("Remove this worktree AND force-delete its unmerged branch?"
                        f"\n\n{path}")
            title = "Delete unmerged branch"
        else:
            question = ("Remove this clean worktree? An unmerged branch will be "
                        f"preserved.\n\n{path}")
            title = "Clean up worktree"
        if not _confirm(title, question, danger=True):
            return
        controller = self.ctx.controller
        repository = self.repository()
        delete_branch = self._delete_branch

        def cleanup() -> object:
            return controller.cleanup_worktree(repository, path, delete_branch)

        def done(result: object) -> None:
            payload = result if isinstance(result, dict) else {}
            if payload.get("deleted_branch"):
                self.ctx.toast("Worktree removed",
                               f"Branch {payload.get('branch')} was deleted.", "success")
            else:
                self.ctx.toast("Worktree removed",
                               f"Branch {payload.get('branch')} was preserved.", "success")
            self.changed.emit()

        self.submit("cleanup", cleanup, done)

    def _open_folder(self) -> None:
        entry = self._entry
        if not entry:
            return
        try:
            open_path_external(str(entry.get("path") or ""))
        except OSError as error:
            self.ctx.toast("Unable to open folder", str(error), "error")


def _confirm(title: str, message: str, danger: bool = False) -> bool:
    from PySide6.QtWidgets import QMessageBox

    icon = (QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    box = QMessageBox(icon, title, message, buttons, None)
    return box.exec() == QMessageBox.StandardButton.Yes


# ---------------------------------------------------------------------------
# artifact detail
# ---------------------------------------------------------------------------

class ArtifactDetailPanel(DetailPanel):
    """One artifact: metadata plus text preview read through the store."""

    def _build_body(self) -> None:
        self._grid = self.add_grid()
        self._truncated = faint("")
        self._body_layout.addWidget(self._truncated)
        self._text = LogText()
        self._body_layout.addWidget(self._text, stretch=1)
        self._artifact: dict | None = None

    def set_artifact(self, entry: dict | None) -> None:
        self._artifact = entry
        if not entry:
            self.show_placeholder("No artifact selected",
                                  "Pick an artifact to preview its contents.")
            return
        self.set_heading(str(entry.get("name") or entry.get("kind") or "Artifact"))
        self._grid.clear()
        self._grid.add_row("Kind", entry.get("kind"))
        self._grid.add_row("Size", format_size(entry.get("size_bytes")))
        self._grid.add_row("Workflow", short_id(entry.get("workflow_id"), 8))
        self._grid.add_row("Task", short_id(entry.get("task_id"), 8))
        self._grid.add_row("Created", format_timestamp(entry.get("created_at")))
        self._truncated.setText("")
        self._text.set_log("")
        self.show_content()
        controller = self.ctx.controller
        artifact_id = str(entry.get("id") or "")
        if artifact_id:
            self.submit(
                "read",
                lambda: controller.read_artifact(self.repository(), artifact_id),
                self._on_read,
                lambda message: self._text.set_log(f"Unable to read artifact: {message}"),
            )

    def _on_read(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        text = str(data.get("text", "")).rstrip()
        self._text.set_log(text or "(empty artifact)")
        self._truncated.setText("Preview truncated; the artifact file holds the full content."
                                if len(text) >= 65000 else "")


# ---------------------------------------------------------------------------
# log viewer
# ---------------------------------------------------------------------------

class LogPanel(DetailPanel):
    """Contextual log viewer with path actions (used in task/run detail)."""

    def _build_body(self) -> None:
        self.add_header_action("Copy path", self._copy_path, variant="ghost")
        self.add_header_action("Open folder", self._open_folder, variant="ghost")
        self._path_label = faint("")
        self._body_layout.addWidget(self._path_label)
        self._text = LogText()
        self._body_layout.addWidget(self._text, stretch=1)
        self._entry: dict | None = None

    def set_log(self, data: dict | None) -> None:
        """Fill from a ``read_log`` payload (name/path/truncated/text)."""
        self._entry = data
        if not data:
            self.show_placeholder("No log selected", "Pick a log to read it.")
            return
        self.set_heading(str(data.get("name") or "Log"))
        self._path_label.setText(str(data.get("path") or ""))
        text = str(data.get("text", "")).rstrip()
        if data.get("truncated"):
            text = "(showing the most recent part of this log)\n" + text
        self._text.set_log(text or "(empty log)")
        self.show_content()

    def _copy_path(self) -> None:
        path = str((self._entry or {}).get("path") or "")
        if not path:
            return
        QApplication_clipboard().setText(path)
        self.ctx.toast("Copied", "Log path copied to the clipboard.", "info")

    def _open_folder(self) -> None:
        path = str((self._entry or {}).get("path") or "")
        if not path:
            return
        try:
            open_path_external(Path(path).parent)
        except OSError as error:
            self.ctx.toast("Unable to open folder", str(error), "error")
