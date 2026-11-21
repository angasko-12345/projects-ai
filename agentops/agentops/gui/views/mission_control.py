"""Mission Control: a local, cross-repository status overview and task entry.

Design read: desktop project overview for AgentOps users, adapting ObsidianUI's
clear panel hierarchy to the existing Qt palette. ENERGY 2 / RHYTHM 1 / MOTION 1.
The repository table is the focal point because it answers which project needs
attention; the existing dashboard palette and spacing preserve product identity.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from ..tokens import DARK
from ..widgets import Card, PageHeader, TableColumn, TablePanel, ghost_button, muted
from .base import BaseView, ViewContext

_REPO_LIMIT = 8
_GIT_TIMEOUT_SECONDS = 5
_PROBE_KEY = "mission-control-git-probe"


@dataclass(frozen=True)
class RepoStatus:
    """Bounded status facts for one configured repository."""

    path: str
    name: str
    branch: str
    dirty: bool | None
    is_git_repo: bool
    error: str | None = None
    repository_root: str | None = None
    workflow_history_available: bool = False
    workflow_count: int = 0
    active_workflows: int = 0
    needs_attention: int = 0


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one fixed-argv, non-interactive Git probe with a short timeout."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    # Do not let a caller's Git environment silently redirect the selected repo.
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    ):
        env.pop(key, None)
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
        env=env,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _unavailable(path: str, name: str, reason: str, branch: str) -> RepoStatus:
    return RepoStatus(
        path=path,
        name=name,
        branch=branch,
        dirty=None,
        is_git_repo=False,
        error=reason,
    )


def _probe_one_repo(path: str) -> RepoStatus:
    """Probe one repository without exposing Git stderr or file names."""
    name = Path(path.replace("\\", "/").rstrip("/")).name or "Repository"
    repo = Path(path)
    try:
        if not repo.is_dir():
            return _unavailable(path, name, "missing", "Missing")

        root_result = _run_git(repo, "rev-parse", "--show-toplevel")
        if root_result.returncode != 0:
            return _unavailable(path, name, "not_a_repo", "Not a Git repo")
        root = Path(root_result.stdout.strip())

        branch_result = _run_git(repo, "branch", "--show-current")
        if branch_result.returncode != 0:
            return _unavailable(path, name, "branch_failed", "Unavailable")
        branch = branch_result.stdout.strip()
        if not branch:
            head_result = _run_git(repo, "rev-parse", "--verify", "HEAD")
            if head_result.returncode == 0:
                branch = "(detached)"
            else:
                symbolic_result = _run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
                if symbolic_result.returncode == 0 and symbolic_result.stdout.strip():
                    branch = symbolic_result.stdout.strip()
                else:
                    return _unavailable(path, name, "branch_failed", "Unavailable")

        status_result = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        if status_result.returncode != 0:
            return RepoStatus(
                path=path,
                name=name,
                branch=branch,
                dirty=None,
                is_git_repo=True,
                error="status_failed",
                repository_root=root.as_posix(),
            )

        return RepoStatus(
            path=path,
            name=name,
            branch=branch,
            dirty=bool(status_result.stdout),
            is_git_repo=True,
            repository_root=root.as_posix(),
        )
    except subprocess.TimeoutExpired:
        return _unavailable(path, name, "timeout", "Timeout")
    except OSError:
        return _unavailable(path, name, "unavailable", "Unavailable")


def _read_workflow_counts(repository_root: str) -> dict[str, int] | None:
    """Read bounded workflow totals from an already-existing AgentOps database."""
    state_path = Path(repository_root) / ".agentops" / "state.sqlite"
    wal_path = Path(f"{state_path}-wal")
    journal_path = Path(f"{state_path}-journal")

    def file_signature(path: Path) -> tuple[int, int, int, int, int, int] | None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("state database sidecar is not a regular file")
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
        )

    try:
        database_signature = file_signature(state_path)
        if database_signature is None:
            return None
        # A hot rollback journal needs recovery, which would write to the source DB.
        if file_signature(journal_path) is not None:
            return None
        wal_signature = file_signature(wal_path)

        # Copy only the existing DB and WAL. Never open the source with SQLite: a
        # normal StateStore open initializes schema and WAL readers can alter SHM.
        with tempfile.TemporaryDirectory(prefix="agentops-mission-control-") as temporary:
            snapshot_path = Path(temporary) / "state.sqlite"
            shutil.copyfile(state_path, snapshot_path)
            if wal_signature is not None:
                shutil.copyfile(wal_path, Path(f"{snapshot_path}-wal"))

            # The snapshot is valid only if its source files stayed stable for the
            # entire copy. A missing/changed WAL or a concurrent journal is unknown.
            if (
                file_signature(state_path) != database_signature
                or file_signature(wal_path) != wal_signature
                or file_signature(journal_path) is not None
            ):
                return None

            uri = snapshot_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1)
            try:
                connection.row_factory = sqlite3.Row
                workflow_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(workflows)")
                }
                failure_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(failures)")
                }
                if "status" not in workflow_columns or not {
                    "created_at",
                    "retryable",
                    "repairable",
                    "recovery_state",
                }.issubset(failure_columns):
                    return None

                total = int(connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0])
                active = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workflows WHERE status IN (?, ?)",
                        ("pending", "running"),
                    ).fetchone()[0]
                )
                failures = connection.execute(
                    """
                    SELECT retryable, repairable, recovery_state
                    FROM failures
                    ORDER BY created_at, rowid
                    LIMIT ?
                    """,
                    (100,),
                ).fetchall()
                attention = sum(
                    1 for failure in failures
                    if failure["retryable"] or failure["repairable"] or failure["recovery_state"]
                )
                return {"total": total, "active": active, "attention": attention}
            finally:
                connection.close()
    except Exception:
        # A missing, unreadable, or incompatible history store is unknown, not zero.
        return None


def _configured_paths(repository: str, recent: list[str]) -> list[str]:
    """Return the current repository and a small, normalized recent list."""
    paths: list[str] = []
    seen: set[str] = set()
    for path in [repository, *recent]:
        if not path:
            continue
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        paths.append(path)
        if len(paths) >= _REPO_LIMIT:
            break
    return paths


def _probe_repos(paths: list[str]) -> list[RepoStatus]:
    """Probe recent repositories serially in one bounded controller worker."""
    results: list[RepoStatus] = []
    for path in paths:
        status = _probe_one_repo(path)
        if status.is_git_repo and status.error is None and status.repository_root:
            counts = _read_workflow_counts(status.repository_root)
            if counts is not None:
                status = RepoStatus(
                    path=status.path,
                    name=status.name,
                    branch=status.branch,
                    dirty=status.dirty,
                    is_git_repo=True,
                    repository_root=status.repository_root,
                    workflow_history_available=True,
                    workflow_count=counts["total"],
                    active_workflows=counts["active"],
                    needs_attention=counts["attention"],
                )
        results.append(status)
    return results


_REPO_COLUMNS = (
    TableColumn("name", "Repository", 190, stretch=True),
    TableColumn("branch", "Branch", 160),
    TableColumn("status", "Git status", 100),
    TableColumn("workflows", "Workflows", 100),
    TableColumn("active", "Active", 110),
    TableColumn("attention", "Recent attention", 125),
)


class MissionControlView(BaseView):
    """Recent repository overview with entry to the existing task workflow."""

    view_id = "mission_control"
    title = "Mission Control"

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        self._current_repo_paths: list[str] = []
        self._status_loaded = False
        self._probe_running = False
        self._probe_rerun = False
        super().__init__(context, parent)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DARK.space_page, DARK.space_page, DARK.space_page, DARK.space_page)
        layout.setSpacing(DARK.space_md)

        self._page_header = PageHeader(self.title)
        self._page_header.set_subtitle("Current and saved recent repositories")
        new_task = ghost_button("New Task")
        new_task.setAccessibleName("New Task in selected repository")
        new_task.clicked.connect(self._new_task_in_selected_repo)
        self._new_task_button = new_task
        self._page_header.add_action(new_task)
        refresh = ghost_button("Refresh")
        refresh.setAccessibleName("Refresh repository status")
        refresh.clicked.connect(self.refresh)
        self._page_header.add_action(refresh)
        layout.addWidget(self._page_header)

        table_card = Card("Repositories")
        self._table = TablePanel(
            _REPO_COLUMNS,
            empty_message="No repositories saved",
            empty_detail="Choose a repository with Ctrl+K or add one in Settings. This view checks only the current and saved recent repositories.",
        )
        self._table.setMinimumHeight(280)
        self._table.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.view.customContextMenuRequested.connect(self._show_context_menu)
        self._table.activated.connect(self._on_row_activated)
        self._update_new_task_button()
        table_card.body().addWidget(self._table)
        layout.addWidget(table_card, stretch=1)

        probe_note = muted("Git status and saved workflow totals are read locally. Mission Control does not fetch or start workflows.")
        probe_note.setWordWrap(True)
        layout.addWidget(probe_note)

        workflow_notice = muted(
            "New Task uses AgentOps's existing workflow. After verification and review succeed, it may commit and merge automatically. Mission Control does not add a human approval step."
        )
        workflow_notice.setWordWrap(True)
        layout.addWidget(workflow_notice)

    def refresh(self) -> None:
        """Reload the current repository and bounded list of saved recent repos."""
        settings = self.ctx.settings()
        paths = _configured_paths(settings.repository or "", settings.recent_repositories)
        self._current_repo_paths = paths
        self._status_loaded = False
        self._update_new_task_button()
        if not paths:
            self._probe_rerun = self._probe_running
            self._show_empty()
            return

        self._page_header.set_subtitle("Checking current and recent repositories")
        self._table.set_rows([])
        self._table.set_empty_state(
            "Checking repositories",
            "Reading local Git status and any existing AgentOps workflow history.",
        )
        if self._probe_running:
            self._probe_rerun = True
            return
        self._start_probe(paths)

    def _start_probe(self, paths: list[str]) -> None:
        self._probe_running = True
        requested_paths = paths.copy()
        self.submit(
            _PROBE_KEY,
            lambda: _probe_repos(requested_paths),
            self._on_probe_result,
            self._on_probe_error,
        )

    def _start_queued_refresh(self) -> bool:
        """After the current job ends, run one refresh for the latest settings."""
        if not self._probe_rerun:
            return False
        self._probe_running = False
        self._probe_rerun = False
        if self._current_repo_paths:
            self._start_probe(self._current_repo_paths)
        else:
            self._show_empty()
        return True

    def _on_probe_error(self, _message: str) -> None:
        if self._start_queued_refresh():
            return
        self._probe_running = False
        if not self._current_repo_paths:
            self._show_empty()
            return
        self._status_loaded = False
        self._table.set_rows([])
        self._table.set_empty_state(
            "Repository check failed",
            "Refresh to try again. No repository status was marked clean.",
        )
        self._page_header.set_subtitle("Repository status could not be checked")
        self._update_new_task_button()

    def _on_probe_result(self, results: list[RepoStatus]) -> None:
        """Render a complete result only when it matches the current repo list."""
        if self._start_queued_refresh():
            return
        self._probe_running = False
        if [status.path for status in results] != self._current_repo_paths:
            return
        rows = [self._status_to_row(status) for status in results]
        self._table.set_rows(rows)
        if not rows:
            self._show_empty()
        else:
            self._page_header.set_subtitle(f"{len(rows)} repositories checked locally")
        self._status_loaded = True
        self._update_new_task_button()

    def _status_to_row(self, status: RepoStatus) -> dict[str, object]:
        if status.error:
            status_text = "Unavailable"
            branch_text = {
                "missing": "Missing",
                "not_a_repo": "Not a Git repo",
                "timeout": "Timeout",
                "status_failed": "Status unavailable",
                "branch_failed": "Branch unavailable",
            }.get(status.error, "Unavailable")
        else:
            branch_text = status.branch
            status_text = "Dirty" if status.dirty is True else "Clean" if status.dirty is False else "Unknown"

        if status.workflow_history_available:
            workflows = str(status.workflow_count)
            active = str(status.active_workflows)
            attention = str(status.needs_attention)
        else:
            workflows = active = attention = "Unavailable"

        return {
            "name": status.name,
            "branch": branch_text,
            "status": status_text,
            "workflows": workflows,
            "active": active,
            "attention": attention,
            # Kept as action data only. There is deliberately no path column.
            "path": status.path,
        }

    def _show_empty(self) -> None:
        self._table.set_rows([])
        self._table.set_empty_state(
            "No repositories saved",
            "Choose a repository with Ctrl+K or add one in Settings. This view checks only the current and saved recent repositories.",
        )
        self._page_header.set_subtitle("Current and saved recent repositories")
        self._status_loaded = True
        self._update_new_task_button()

    def _selected_repo_row(self) -> dict[str, object] | None:
        selected = str(self.ctx.repository() or "")
        if not selected:
            return None
        selected_key = os.path.normcase(os.path.normpath(selected))
        for row in self._table.model.rows:
            path = row.get("path")
            if isinstance(path, str) and os.path.normcase(os.path.normpath(path)) == selected_key:
                return row
        return None

    def _update_new_task_button(self) -> None:
        button = getattr(self, "_new_task_button", None)
        if button is None:
            return
        row = self._selected_repo_row()
        button.setEnabled(self._status_loaded and row is not None and self._can_start_task(row))

    def _new_task_in_selected_repo(self) -> None:
        row = self._selected_repo_row()
        if row is not None:
            self._new_task_in_repo(row)

    def _can_start_task(self, row_data: dict[str, object]) -> bool:
        return row_data.get("status") in ("Clean", "Dirty") and not self.ctx.operation_active()

    def _show_context_menu(self, pos) -> None:
        row_data = self._table.row_of(self._table.view.indexAt(pos))
        if row_data is None:
            return

        menu = QMenu(self)
        can_switch = not self.ctx.operation_active()
        switch_action = menu.addAction("Switch to this repository")
        switch_action.setEnabled(can_switch)
        switch_action.triggered.connect(lambda: self._switch_repo(str(row_data["path"])))

        new_task_action = menu.addAction("New Task in this repository...")
        new_task_action.setEnabled(self._can_start_task(row_data))
        new_task_action.triggered.connect(lambda: self._new_task_in_repo(row_data))

        open_workflows_action = menu.addAction("Open Workflows")
        open_workflows_action.setEnabled(can_switch)
        open_workflows_action.triggered.connect(lambda: self._open_workflows(str(row_data["path"])))
        menu.exec(self._table.view.viewport().mapToGlobal(pos))

    def _on_row_activated(self, row_data: dict[str, object]) -> None:
        if self._can_start_task(row_data):
            self._new_task_in_repo(row_data)
        elif self.ctx.operation_active():
            self.ctx.toast(
                "Operation in progress",
                "Cancel the running task before changing repositories or starting another.",
                "warning",
            )

    def _switch_repo(self, path: str) -> None:
        if self.ctx.operation_active():
            self.ctx.toast("Operation in progress", "Cancel the running task before changing repositories.", "warning")
            return
        self.ctx.update_settings({"repository": path})
        self.ctx.navigate("workflows")

    def _new_task_in_repo(self, row_data: dict[str, object]) -> None:
        if self.ctx.operation_active():
            self.ctx.toast("Operation in progress", "Cancel the running task before starting another.", "warning")
            return
        if not self._can_start_task(row_data):
            return
        self.ctx.update_settings({"repository": str(row_data["path"])})
        if callable(self.ctx.new_task):
            self.ctx.new_task("", None)

    def _open_workflows(self, path: str) -> None:
        if self.ctx.operation_active():
            self.ctx.toast("Operation in progress", "Cancel the running task before changing repositories.", "warning")
            return
        self.ctx.update_settings({"repository": path})
        self.ctx.navigate("workflows")

    def on_operation_event(self, event: dict) -> None:
        if str(event.get("kind") or "") in ("workflow-started", "workflow-result", "thread-finished"):
            self.refresh()

    def shutdown(self) -> None:
        super().shutdown()
