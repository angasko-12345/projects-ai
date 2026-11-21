"""Mission Control tests using disposable Git repositories and offscreen Qt."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QLabel

try:
    from tests.qt_display import destroy, qt_app, wait, wait_until
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, wait, wait_until

from agentops.gui.bridge import ControllerBridge
from agentops.gui.context import ViewContext
from agentops.gui.settings import AppSettings
from agentops.gui.views.mission_control import (
    MissionControlView,
    RepoStatus,
    _configured_paths,
    _probe_one_repo,
    _probe_repos,
    _read_workflow_counts,
    _run_git,
)
from agentops.state import StateStore


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _sqlite_file_state(database: Path) -> dict[str, tuple[object, ...]]:
    """Capture DB and journal-sidecar contents plus stable metadata for regression checks."""
    state: dict[str, tuple[object, ...]] = {}
    for path in (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    ):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        state[path.name] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
            path.read_bytes(),
        )
    return state


def _create_history_database(path: Path, *, wal: bool) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    mode = connection.execute(f"PRAGMA journal_mode = {'WAL' if wal else 'DELETE'}").fetchone()[0]
    if wal:
        assert mode.lower() == "wal"
    connection.executescript(
        """
        CREATE TABLE workflows (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE failures (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            retryable INTEGER NOT NULL DEFAULT 0,
            repairable INTEGER NOT NULL DEFAULT 0,
            recovery_state TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO workflows (id, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("w1", "first", "running", "2026-01-01", "2026-01-01"),
            ("w2", "second", "pending", "2026-01-02", "2026-01-02"),
            ("w3", "third", "completed", "2026-01-03", "2026-01-03"),
        ],
    )
    connection.executemany(
        "INSERT INTO failures (id, created_at, retryable, repairable, recovery_state) VALUES (?, ?, ?, ?, ?)",
        [
            ("f1", "2026-01-01", 0, 0, None),
            ("f2", "2026-01-02", 1, 0, None),
            ("f3", "2026-01-03", 0, 0, "recovered"),
        ],
    )
    connection.commit()
    return connection


def _make_git_repo(path: Path, *, commit: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Mission Control Test")
    if commit:
        (path / "README.md").write_text("test\n", encoding="utf-8")
        _git(path, "add", "README.md")
        _git(path, "commit", "-m", "initial")
    return path


def _status(repo: Path, *, name: str | None = None, dirty: bool = False) -> RepoStatus:
    return RepoStatus(
        path=str(repo),
        name=name or repo.name,
        branch="main",
        dirty=dirty,
        is_git_repo=True,
    )


class MissionControlGitProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mission_control_probe_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_clean_and_dirty_repositories(self):
        clean = _make_git_repo(self.temp_dir / "clean")
        self.assertFalse(_probe_one_repo(str(clean)).dirty)

        modified = _make_git_repo(self.temp_dir / "modified")
        (modified / "README.md").write_text("changed\n", encoding="utf-8")
        self.assertTrue(_probe_one_repo(str(modified)).dirty)

        untracked = _make_git_repo(self.temp_dir / "untracked")
        (untracked / "file with spaces.txt").write_text("new\n", encoding="utf-8")
        self.assertTrue(_probe_one_repo(str(untracked)).dirty)

        staged = _make_git_repo(self.temp_dir / "staged")
        (staged / "README.md").write_text("staged\n", encoding="utf-8")
        _git(staged, "add", "README.md")
        self.assertTrue(_probe_one_repo(str(staged)).dirty)

    def test_detached_branch_and_unborn_repository(self):
        detached = _make_git_repo(self.temp_dir / "detached")
        first = _git(detached, "rev-parse", "HEAD")
        (detached / "next.txt").write_text("next\n", encoding="utf-8")
        _git(detached, "add", "next.txt")
        _git(detached, "commit", "-m", "second")
        _git(detached, "checkout", "--detach", first)
        self.assertEqual(_probe_one_repo(str(detached)).branch, "(detached)")

        unborn = _make_git_repo(self.temp_dir / "unborn", commit=False)
        (unborn / "README.md").write_text("untracked\n", encoding="utf-8")
        status = _probe_one_repo(str(unborn))
        self.assertTrue(status.is_git_repo)
        self.assertIn(status.branch, ("main", "master"))
        self.assertTrue(status.dirty)

    def test_missing_and_non_git_paths_are_unavailable(self):
        missing = _probe_one_repo(str(self.temp_dir / "missing"))
        self.assertEqual(missing.error, "missing")
        self.assertIsNone(missing.dirty)

        plain = self.temp_dir / "plain"
        plain.mkdir()
        non_repo = _probe_one_repo(str(plain))
        self.assertEqual(non_repo.error, "not_a_repo")
        self.assertIsNone(non_repo.dirty)

    def test_timeout_is_unknown_not_clean(self):
        repo = self.temp_dir / "slow"
        repo.mkdir()
        with patch(
            "agentops.gui.views.mission_control._run_git",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            status = _probe_one_repo(str(repo))
        self.assertEqual(status.error, "timeout")
        self.assertIsNone(status.dirty)

    def test_git_command_has_no_optional_locks_and_no_prompt(self):
        repo = self.temp_dir / "args"
        repo.mkdir()
        completed = subprocess.CompletedProcess(["git"], 0, "", "")
        with patch("agentops.gui.views.mission_control.subprocess.run", return_value=completed) as run:
            _run_git(repo, "status", "--porcelain=v1")
        args = run.call_args.args[0]
        self.assertIn("--no-optional-locks", args)
        self.assertIn("core.fsmonitor=false", args)
        self.assertIn("status", args)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("GIT_DIR", env)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_failed_status_is_unavailable_not_dirty_or_clean(self):
        repo = self.temp_dir / "status-fails"
        _make_git_repo(repo)
        good = subprocess.CompletedProcess(["git"], 0, str(repo), "")
        branch = subprocess.CompletedProcess(["git"], 0, "main", "")
        failed = subprocess.CompletedProcess(["git"], 1, "", "private stderr")
        with patch(
            "agentops.gui.views.mission_control._run_git",
            side_effect=[good, branch, failed],
        ):
            status = _probe_one_repo(str(repo))
        self.assertEqual(status.error, "status_failed")
        self.assertIsNone(status.dirty)
        self.assertNotIn("private stderr", repr(status))

    def test_git_probe_does_not_mutate_or_fetch(self):
        repo = _make_git_repo(self.temp_dir / "no_mutation")
        _git(repo, "remote", "add", "origin", "https://example.invalid/private.git")
        original_head = _git(repo, "rev-parse", "HEAD")
        original_status = _git(repo, "status", "--porcelain")
        for _ in range(2):
            self.assertTrue(_probe_one_repo(str(repo)).is_git_repo)
        self.assertEqual(_git(repo, "rev-parse", "HEAD"), original_head)
        self.assertEqual(_git(repo, "status", "--porcelain"), original_status)

    def test_absent_history_is_not_created_and_existing_zero_is_distinct(self):
        no_history = _make_git_repo(self.temp_dir / "no_history")
        result = _probe_repos([str(no_history)])[0]
        self.assertFalse(result.workflow_history_available)
        self.assertFalse((no_history / ".agentops" / "state.sqlite").exists())

        existing = _make_git_repo(self.temp_dir / "existing_history")
        state_path = existing / ".agentops" / "state.sqlite"
        state = StateStore(state_path)
        state.close()
        before = _sqlite_file_state(state_path)
        result = _probe_repos([str(existing)])[0]
        after = _sqlite_file_state(state_path)
        self.assertTrue(result.workflow_history_available)
        self.assertEqual(result.workflow_count, 0)
        self.assertEqual(result.active_workflows, 0)
        self.assertEqual(result.needs_attention, 0)
        self.assertEqual(after, before)

    def test_history_read_preserves_an_existing_rollback_journal_database(self):
        repository = self.temp_dir / "rollback_history"
        database = repository / ".agentops" / "state.sqlite"
        connection = _create_history_database(database, wal=False)
        connection.close()

        before = _sqlite_file_state(database)
        counts = _read_workflow_counts(str(repository))
        after = _sqlite_file_state(database)

        self.assertEqual(counts, {"total": 3, "active": 2, "attention": 2})
        self.assertEqual(after, before)

    def test_history_read_preserves_wal_database_and_sidecars(self):
        repository = self.temp_dir / "wal_history"
        database = repository / ".agentops" / "state.sqlite"
        connection = _create_history_database(database, wal=True)
        self.addCleanup(connection.close)
        self.assertTrue(Path(f"{database}-wal").is_file())
        self.assertGreater(Path(f"{database}-wal").stat().st_size, 0)

        before = _sqlite_file_state(database)
        counts = _read_workflow_counts(str(repository))
        after = _sqlite_file_state(database)

        self.assertEqual(counts, {"total": 3, "active": 2, "attention": 2})
        self.assertEqual(after, before)

    def test_recent_repository_list_is_deduplicated_and_capped(self):
        paths = _configured_paths("/work/repo0", ["/work/repo0/.", *[f"/work/repo{i}" for i in range(1, 12)]])
        self.assertEqual(len(paths), 8)
        self.assertEqual(len({path.casefold() for path in paths}), 8)
        self.assertEqual(paths[0], "/work/repo0")


class MissionControlViewTests(unittest.TestCase):
    def setUp(self):
        qt_app()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mission_control_view_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.repo1 = _make_git_repo(self.temp_dir / "repo1")
        self.repo2 = _make_git_repo(self.temp_dir / "repo2")

        self.controller = MagicMock()
        self.bridge = ControllerBridge(self.controller)
        self.settings = AppSettings()
        self.settings.touch_repository(str(self.repo1))
        self.settings.recent_repositories = [str(self.repo1), str(self.repo2)]
        self.updates: list[dict] = []
        self.navigation: list[str] = []
        self.new_task_calls: list[tuple[object, ...]] = []
        self.toasts: list[tuple[object, ...]] = []
        self.active = False

        def update(values):
            self.updates.append(values.copy())
            self.settings.__dict__.update(values)

        self.context = ViewContext(
            bridge=self.bridge,
            repository=lambda: self.settings.repository,
            navigate=self.navigation.append,
            toast=lambda *args, **kwargs: self.toasts.append(args),
            settings=lambda: self.settings,
            open_task=lambda *_args: None,
            open_run=lambda *_args: None,
            operation_active=lambda: self.active,
            update_settings=update,
            new_task=lambda *args, **kwargs: self.new_task_calls.append(args),
            start_task=lambda *_args, **_kwargs: True,
        )
        self.view = MissionControlView(self.context)
        self.addCleanup(self._cleanup_view)
        self.view.show()
        self.view.refresh()
        wait_until(lambda: self.view._status_loaded, timeout_ms=8000)

    def _cleanup_view(self):
        if hasattr(self, "view"):
            destroy(self.view)
        self.bridge.shutdown()

    def _row(self, name: str) -> dict[str, object]:
        for row in self.view._table.model.rows:
            if row["name"] == name:
                return row
        self.fail(f"Repository row {name!r} not found")

    def test_view_shows_real_repository_status_and_unavailable_history(self):
        self.assertEqual(self._row("repo1")["status"], "Clean")
        self.assertEqual(self._row("repo1")["workflows"], "Unavailable")
        self.assertEqual(self._row("repo1")["active"], "Unavailable")
        self.assertEqual(self._row("repo1")["attention"], "Unavailable")
        self.assertEqual(self._row("repo2")["status"], "Clean")

    def test_full_paths_are_action_data_not_visible_columns(self):
        row = self._row("repo1")
        self.assertEqual(row["path"], str(self.repo1))
        visible_keys = {column.key for column in self.view._table.model.columns}
        self.assertNotIn("path", visible_keys)
        self.assertNotIn(str(self.repo1), [self.view._table.model.data(self.view._table.model.index(0, col)) for col in range(self.view._table.model.columnCount())])

    def test_task_entry_switches_selected_repository_before_opening_standard_dialog(self):
        row = self._row("repo2")
        self.view._new_task_in_repo(row)
        self.assertEqual(self.settings.repository, str(self.repo2))
        self.assertEqual(self.new_task_calls, [("", None)])
        self.assertEqual(len(self.updates), 1)

    def test_header_new_task_uses_the_selected_repository(self):
        self.assertTrue(self.view._new_task_button.isEnabled())
        self.view._new_task_button.click()
        self.assertEqual(self.settings.repository, str(self.repo1))
        self.assertEqual(self.new_task_calls, [("", None)])

    def test_active_operation_blocks_repo_switch_workflows_and_task_entry(self):
        row = self._row("repo2")
        self.active = True
        self.view.on_operation_event({"kind": "workflow-started"})
        self.assertFalse(self.view._new_task_button.isEnabled())
        self.view._switch_repo(str(self.repo2))
        self.view._open_workflows(str(self.repo2))
        self.view._new_task_in_repo(row)
        self.assertEqual(self.updates, [])
        self.assertEqual(self.navigation, [])
        self.assertEqual(self.new_task_calls, [])
        self.assertEqual(len(self.toasts), 3)

    def test_opens_existing_workflows_for_selected_repository(self):
        self.view._open_workflows(str(self.repo2))
        self.assertEqual(self.settings.repository, str(self.repo2))
        self.assertEqual(self.navigation, ["workflows"])

    def test_task_copy_discloses_existing_automatic_commit_and_merge(self):
        visible_text = " ".join(label.text() for label in self.view.findChildren(QLabel))
        self.assertIn("commit and merge automatically", visible_text)
        self.assertIn("does not add a human approval step", visible_text)

    def test_refresh_coalesces_requests_and_reruns_the_latest_snapshot(self):
        started = threading.Event()
        release_old = threading.Event()
        lock = threading.Lock()
        calls = 0
        active_calls = 0
        max_active_calls = 0

        def delayed_probe(paths):
            nonlocal calls, active_calls, max_active_calls
            with lock:
                calls += 1
                call_number = calls
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            try:
                if call_number == 1:
                    started.set()
                    release_old.wait(5)
                result_name = "Stale result" if call_number == 1 else "Latest result"
                return [
                    _status(Path(path), name=result_name if index == 0 else Path(path).name, dirty=call_number == 1)
                    for index, path in enumerate(paths)
                ]
            finally:
                with lock:
                    active_calls -= 1

        with patch("agentops.gui.views.mission_control._probe_repos", side_effect=delayed_probe):
            self.view.refresh()
            self.assertTrue(started.wait(2))
            self.view.refresh()
            with lock:
                self.assertEqual(calls, 1)
            release_old.set()
            wait_until(
                lambda: any(row["name"] == "Latest result" for row in self.view._table.model.rows),
                timeout_ms=3000,
            )
        self.assertEqual(self.view._table.model.rows[0]["name"], "Latest result")
        self.assertEqual(calls, 2)
        self.assertEqual(max_active_calls, 1)

    def test_error_state_offers_retry_without_claiming_clean(self):
        self.view._on_probe_error("raw error text should not surface")
        labels = " ".join(label.text() for label in self.view.findChildren(QLabel))
        self.assertIn("Repository check failed", labels)
        self.assertIn("Refresh to try again", labels)
        self.assertNotIn("raw error text", labels)
        self.assertEqual(self.view._table.model.rowCount(), 0)

    def test_unavailable_rows_cannot_start_tasks(self):
        unavailable = self.view._status_to_row(
            RepoStatus(path="/missing", name="missing", branch="Missing", dirty=None, is_git_repo=False, error="missing")
        )
        self.assertEqual(unavailable["status"], "Unavailable")
        self.assertFalse(self.view._can_start_task(unavailable))

    def test_empty_repository_settings_have_explanatory_state(self):
        empty_settings = AppSettings()
        empty_context = ViewContext(
            bridge=self.bridge,
            repository=lambda: "",
            navigate=lambda _view: None,
            toast=lambda *_args: None,
            settings=lambda: empty_settings,
            open_task=lambda *_args: None,
            open_run=lambda *_args: None,
            operation_active=lambda: False,
            update_settings=lambda _values: None,
            new_task=lambda *_args: None,
            start_task=lambda *_args: True,
        )
        empty_view = MissionControlView(empty_context)
        self.addCleanup(lambda: destroy(empty_view))
        empty_view.refresh()
        self.assertIs(empty_view._table._stack.currentWidget(), empty_view._table._empty)
        text = " ".join(label.text() for label in empty_view.findChildren(QLabel))
        self.assertIn("No repositories saved", text)

    def test_keyboard_table_activation_routes_to_task_entry(self):
        row = self._row("repo1")
        self.view._on_row_activated(row)
        self.assertEqual(self.new_task_calls, [("", None)])


class MissionControlShellIntegrationTests(unittest.TestCase):
    def setUp(self):
        qt_app()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mission_control_shell_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.repo = _make_git_repo(self.temp_dir / "repo")
        from agentops.gui.shell import MainWindow

        controller = MagicMock()
        controller.task_options.return_value = {
            "routing_enabled": True,
            "verification_profiles": ["default"],
            "default_verification_profile": "default",
        }
        controller.list_agent_profiles.return_value = []
        controller.dashboard_summary.return_value = {
            "repository": str(self.repo),
            "total_workflows": 0,
            "active_workflows": {"pending": 0, "running": 0},
            "task_counts": {},
            "recent_workflows": [],
            "verification_status": {},
            "recent_verifications": [],
            "failure_count": 0,
            "needs_attention": [],
            "recent_failures": [],
            "recent_events": [],
        }
        controller.interrupted_work.return_value = {
            "agent_runs": 0,
            "verification_runs": 0,
            "tasks": 0,
            "total": 0,
        }
        self.window = MainWindow(controller, settings=AppSettings())
        self.window.set_repository(str(self.repo))
        self.window.show()
        wait(50)

    def tearDown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch("agentops.gui.shell.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            destroy(self.window)

    def test_mission_control_is_registered_navigable_and_in_palette(self):
        self.assertIn("mission_control", self.window._views)
        self.window.navigate("mission_control")
        self.assertIs(self.window.active_view(), self.window._views["mission_control"])
        self.window.open_palette()
        wait(30)
        self.assertIn(
            "Open Mission Control",
            [command.title for command in self.window._palette.visible_commands()],
        )

    def test_switching_repository_updates_shell_selection(self):
        view = self.window._views["mission_control"]
        view._switch_repo(str(self.repo))
        self.assertEqual(self.window._repository, str(self.repo))
        self.assertEqual(self.window._settings.repository, str(self.repo))


if __name__ == "__main__":
    unittest.main()
