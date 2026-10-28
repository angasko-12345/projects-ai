"""Phase C1 thread list: painted rows grouped by repository, settled rows
collapsed, selection signal, and multi-repo browsing that never creates a DB."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, make_context, qt_app, requires_qt, wait_until
    from tests.test_gui_qt import FakeController
except ModuleNotFoundError:
    from qt_display import destroy, make_context, qt_app, requires_qt, wait_until
    from test_gui_qt import FakeController

NOW = datetime.now(timezone.utc)


def _wf(wf_id: str, status: str, hours: float) -> dict:
    stamp = (NOW - timedelta(hours=hours)).isoformat()
    return {
        "id": wf_id,
        "description": f"work {wf_id}",
        "status": status,
        "created_at": stamp,
        "updated_at": stamp,
        "task_count": 2,
        "task_counts": {"passed": 1},
    }


@requires_qt
class ThreadListTest(unittest.TestCase):
    def _threads(self, controller, repository, recent):
        from agentops.gui.settings import AppSettings
        from agentops.gui.threads import ThreadList

        qt_app()
        settings = AppSettings(recent_repositories=list(recent))
        context = replace(make_context(controller, repository), settings=lambda: settings)
        threads = ThreadList(context)
        self.addCleanup(destroy, threads)
        return threads

    def kinds(self, threads):
        return [row["kind"] for row in threads.model.rows]

    def test_groups_active_rows_then_a_collapsed_settled_row(self):
        threads = self._threads(FakeController(), "D:/repo", ["D:/repo"])
        threads.set_repository_data(
            "D:/repo",
            {
                "workflows": [
                    _wf("run", "running", 0.1),
                    _wf("old", "passed", 48),
                    _wf("new", "passed", 2),
                ],
                "refs": [{"workflow_id": "run", "branch": "agentops/run"}],
                "runs": [{"workflow_id": "run", "agent": "codex", "role": "implementation"}],
            },
        )
        self.assertEqual(self.kinds(threads), ["group", "thread", "thread", "settled"])
        group, running = threads.model.rows[:2]
        self.assertEqual((group["text"], group["count"]), ("repo", 2))
        self.assertEqual(
            (running["agent"], running["role"], running["branch"], running["progress"]),
            ("codex", "Implement", "agentops/run", "1/2"),
        )
        self.assertEqual(threads.model.rows[3]["text"], "Settled (1)")
        threads.resize(296, 400)
        self.assertFalse(threads.grab().isNull())  # the delegate paints every kind

        threads._on_click(threads.model.index(3))  # expand Settled
        self.assertEqual(self.kinds(threads)[-1], "thread")
        self.assertEqual(threads.model.rows[-1]["id"], "old")

        picked = []
        threads.workflow_selected.connect(lambda repo, wf: picked.append((repo, wf)))
        threads._on_click(threads.model.index(1))
        self.assertEqual(picked, [("D:/repo", "run")])
        self.assertEqual(threads.model.current, ("D:/repo", "run"))

    def test_filters_search_settled_rows_too(self):
        threads = self._threads(FakeController(), "D:/repo", ["D:/repo"])
        threads.set_repository_data(
            "D:/repo", {"workflows": [_wf("run", "running", 0.1), _wf("old", "failed", 48)]}
        )
        threads.set_filter("work old", "")
        self.assertEqual(
            [r.get("id") for r in threads.model.rows if r["kind"] == "thread"], ["old"]
        )
        threads.set_filter("", "pending")
        self.assertEqual(self.kinds(threads), ["group", "empty"])
        self.assertEqual(threads.model.rows[1]["text"], "No matching workflows")

    def test_browsing_a_repo_without_a_state_db_creates_no_file(self):
        from agentops.gui_controller import AgentOpsController
        from agentops.state import StateStore

        base = Path(tempfile.mkdtemp(prefix="agentops-threads-"))
        active, bare, other = base / "active", base / "bare", base / "other"
        for path in (active, bare, other):
            path.mkdir()
        store = StateStore(other / ".agentops" / "state.sqlite")
        store.create_workflow("docs: dark mode toggle")
        store.close()

        from agentops.gui.threads import load_threads

        controller = AgentOpsController()
        self.assertEqual(len(load_threads(controller, str(other))["workflows"]), 1)
        threads = self._threads(controller, str(active), [str(active), str(bare), str(other)])
        threads.poll_others()
        loaded = wait_until(lambda: str(other) in threads._groups, 10000)
        self.assertTrue(loaded, f"errors={threads.errors}")
        titles = [r["title"] for r in threads.model.rows if r["kind"] == "thread"]
        self.assertEqual(titles, ["docs: dark mode toggle"])
        self.assertNotIn(str(bare), threads._groups)
        self.assertFalse((bare / ".agentops").exists())
        self.assertFalse((active / ".agentops").exists())  # the view reads the active repo

    def test_a_pinned_state_path_skips_other_repositories(self):
        from agentops.state import StateStore

        other = Path(tempfile.mkdtemp(prefix="agentops-threads-"))
        StateStore(other / ".agentops" / "state.sqlite").close()  # a DB that would qualify
        controller = FakeController()
        controller.state_path = Path("/pinned/state.sqlite")  # type: ignore[attr-defined]
        threads = self._threads(controller, "D:/repo", ["D:/repo", str(other)])
        threads.poll_others()
        wait_until(lambda: False, 200)
        self.assertEqual([c for c in controller.calls if c[0] == "list_workflows"], [])

    def test_shell_mounts_the_list_in_the_workflows_sidebar(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        settings = AppSettings()
        settings.touch_repository("D:/repo")
        window = MainWindow(
            FakeController(),
            settings=settings,
            settings_file=Path(tempfile.mkdtemp()) / "settings.json",
        )
        self.addCleanup(destroy, window)
        window.resize(1100, 700)
        window.show()
        window.navigate("workflows")
        view = window._views["workflows"]
        for widget in (view._threads, view._wf_search, view._wf_status):
            self.assertTrue(window._sidebar.isAncestorOf(widget))
        self.assertTrue(wait_until(lambda: "thread" in self.kinds(view._threads)))
        self.assertFalse(view._list.isVisible())  # the hidden table behind the filters
        self.assertEqual(view._threads.model.current, ("D:/repo", "wf-1"))  # auto-opened


if __name__ == "__main__":
    unittest.main()
