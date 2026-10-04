"""Focused regression tests for the workflow control center.

Two layers, matching the two layers of the feature:

* :class:`ControlCenterProjectionTests` is display-free and covers the
  Qt-free projections in ``agentops.gui.control_center`` - stage flow, live
  state, verification totals, failures, worktree and merge readiness, and
  cancellation. These run everywhere.
* :class:`ControlCenterViewTests` drives the real view offscreen and covers
  what only a widget can show: the active workflow display, stage
  transitions, task/run/verification/failure selection, cancellation state,
  and the final ready/blocked state.

Every fixture value is a plausible recorded payload. Nothing here invents
progress: where an assertion cares about a number, the number exists in the
fixture.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agentops.gui import control_center as projection

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from tests.test_gui_qt import REPOSITORY, FakeController
except ModuleNotFoundError:
    from qt_display import destroy, qt_app, requires_qt, wait, wait_until
    from test_gui_qt import REPOSITORY, FakeController


NOW = datetime(2026, 10, 3, 12, 35, 0, tzinfo=timezone.utc)


def _workflow(status: str = "running") -> dict:
    return {"id": "wf-1", "description": "ship the control center",
            "status": status, "created_at": "2026-10-03T12:00:00+00:00",
            "updated_at": "2026-10-03T12:34:00+00:00"}


def _task(task_id: str, role: str, status: str, **extra) -> dict:
    return {"id": task_id, "workflow_id": "wf-1", "role": role, "status": status,
            "description": f"{role} work", "assigned_agent": "opencode",
            "attempts": 1, "max_attempts": 3, "result": None, "verified": False,
            "dependencies": (), "created_at": "2026-10-03T12:00:00+00:00",
            "started_at": None, "finished_at": None, **extra}


def _run(run_id: str, task_id: str, status: str, **extra) -> dict:
    return {"id": run_id, "workflow_id": "wf-1", "task_id": task_id, "attempt": 1,
            "agent": "opencode", "model": "gpt-5-codex", "role": "implementation",
            "status": status, "exit_code": None, "duration_seconds": None,
            "started_at": "2026-10-03T12:30:00+00:00", "ended_at": None,
            "worktree": "D:/repo/.agentops/worktrees/wf-1", "files_changed": [],
            "diff_stat": None, "error": None, "structured_result": None, **extra}


def _verification(run_id: str, task_id: str, **extra) -> dict:
    return {"id": run_id, "workflow_id": "wf-1", "task_id": task_id,
            "profile_name": "default", "status": "completed",
            "overall_status": None, "total_checks": 0, "passed_checks": 0,
            "failed_checks": 0, "skipped_checks": 0,
            "started_at": "2026-10-03T12:20:00+00:00", **extra}


def _failure(failure_id: str, task_id: str, **extra) -> dict:
    return {"id": failure_id, "workflow_id": "wf-1", "task_id": task_id,
            "agent_run_id": None, "source": "verification", "category": "test_failure",
            "severity": "high", "retryable": True, "repairable": True,
            "evidence": None, "primary_error": "assert 1 == 2",
            "recommended_action": "retry_same_agent", "attempt": 1,
            "recovery_state": None, "structured_evidence": None,
            "created_at": "2026-10-03T12:22:00+00:00",
            "updated_at": "2026-10-03T12:22:00+00:00", **extra}


def _standard_tasks(**statuses) -> list[dict]:
    """The standard four roles; ``statuses`` overrides any role's state.

    Terminal tasks carry a ``finished_at``: a stage that has stopped, one way
    or the other, was recorded finishing.
    """
    roles = ("architecture", "implementation", "verification", "review")
    terminal = ("passed", "failed", "blocked", "cancelled")
    return [
        _task(f"t-{index + 1}", role, statuses.get(role, "pending"),
              verified=role == "verification" and statuses.get("verification") == "passed",
              result="evidence" if role == "verification" else None,
              finished_at="2026-10-03T12:30:00+00:00"
              if statuses.get(role, "pending") in terminal else None)
        for index, role in enumerate(roles)
    ]


class ControlCenterProjectionTests(unittest.TestCase):
    """The Qt-free layer: everything the control center claims is true."""

    # ------------------------------------------------------------------
    # stage flow
    # ------------------------------------------------------------------
    def test_high_level_workflow_renders_the_five_fixed_stages(self):
        tasks = _standard_tasks(architecture="passed", implementation="running")
        stages = projection.build_stage_flow(_workflow(), tasks)
        self.assertEqual(
            [stage["label"] for stage in stages],
            ["PLAN", "IMPLEMENT", "VERIFY", "REVIEW", "FINALIZE"],
        )
        self.assertEqual(stages[0]["state"], "passed")
        self.assertEqual(stages[1]["state"], "running")
        # Finalize has not started: a running workflow with stages ahead of it
        # is working a stage, not finalizing.
        self.assertEqual(stages[4]["state"], "pending")

    def test_each_stage_reports_state_agent_model_duration_and_verification(self):
        tasks = _standard_tasks(architecture="passed", implementation="running")
        runs = [_run("r-1", "t-2", "running", duration_seconds=12.5)]
        stage = projection.build_stage_flow(_workflow(), tasks, runs)[1]
        self.assertEqual(stage["state"], "running")
        self.assertEqual(stage["agent"], "opencode")
        self.assertEqual(stage["model"], "gpt-5-codex")
        self.assertEqual(stage["duration_text"], "12.5s")
        self.assertEqual(stage["task_status"], "running")
        self.assertEqual(stage["verification"], "not run")

    def test_unknown_duration_is_reported_as_unknown_not_zero(self):
        # A run with no recorded duration must not read as "0.0s complete".
        tasks = _standard_tasks(architecture="passed")
        stages = projection.build_stage_flow(
            _workflow(), tasks, [_run("r-1", "t-1", "completed", duration_seconds=None)])
        self.assertIsNone(stages[0]["duration_seconds"])
        self.assertEqual(stages[0]["duration_text"], "-")

    def test_verification_state_comes_from_the_linked_run(self):
        tasks = _standard_tasks(verification="passed")
        verifications = [_verification("v-1", "t-3", overall_status="failed",
                                       failed_checks=1, total_checks=2)]
        stage = projection.build_stage_flow(
            _workflow("completed"), tasks, (), verifications)[2]
        self.assertEqual(stage["verification"], "failed")
        self.assertEqual(stage["state"], "passed")

    def test_stage_carries_the_relevant_failure_and_completion_time(self):
        tasks = _standard_tasks(verification="failed")
        failures = [_failure("f-1", "t-3")]
        stage = projection.build_stage_flow(_workflow("failed"), tasks, (), (), failures)[2]
        self.assertEqual(stage["state"], "failed")
        self.assertIn("test failure", stage["failure"])
        self.assertIn("assert 1 == 2", stage["failure"])
        self.assertEqual(stage["completed_at"], "2026-10-03T12:30:00+00:00")

    def test_custom_dag_shows_real_dependencies_not_fixed_stages(self):
        tasks = [
            _task("t-a", "architecture", "passed"),
            _task("t-b", "threat_model", "running", dependencies=("t-a",)),
            _task("t-c", "review", "pending", dependencies=("t-a", "t-b")),
        ]
        stages = projection.build_stage_flow(_workflow(), tasks)
        self.assertTrue(projection.is_custom_dag(tasks))
        self.assertEqual([stage["key"] for stage in stages],
                         ["t-a", "t-b", "t-c", projection.FINALIZE_KEY])
        self.assertEqual(stages[1]["depends_on"], ["t-a"])
        self.assertEqual(stages[2]["depends_on"], ["t-a", "t-b"])
        self.assertEqual(stages[1]["label"], "Threat Model")

    def test_custom_dag_drops_dependencies_that_are_not_part_of_the_workflow(self):
        tasks = [_task("t-a", "custom_stage", "pending", dependencies=("gone", "t-a"))]
        stages = projection.build_stage_flow(_workflow(), tasks)
        self.assertEqual(stages[0]["depends_on"], [])

    def test_standard_stage_transitions_track_the_advance(self):
        first = projection.build_stage_flow(
            _workflow(), _standard_tasks(architecture="passed"))
        self.assertEqual(projection.current_stage(first)["label"], "IMPLEMENT")
        second = projection.build_stage_flow(
            _workflow(), _standard_tasks(architecture="passed", implementation="passed",
                                         verification="running"))
        self.assertEqual(projection.current_stage(second)["label"], "VERIFY")
        self.assertEqual(projection.next_stage(second)["label"], "REVIEW")

    def test_a_finished_workflow_points_at_its_last_finished_stage(self):
        tasks = _standard_tasks(**{role: "passed" for role in
                                   ("architecture", "implementation", "verification", "review")})
        stages = projection.build_stage_flow(_workflow("completed"), tasks)
        self.assertEqual(projection.current_stage(stages)["label"], "FINALIZE")
        self.assertIsNone(projection.next_stage(stages))

    def test_finalize_is_running_only_once_every_task_stage_is_behind_it(self):
        tasks = _standard_tasks(**{role: "passed" for role in
                                   ("architecture", "implementation", "verification", "review")})
        stages = projection.build_stage_flow(_workflow("running"), tasks)
        self.assertEqual(stages[-1]["state"], "running")
        self.assertEqual(projection.current_stage(stages)["label"], "FINALIZE")

    # ------------------------------------------------------------------
    # live panel
    # ------------------------------------------------------------------
    def test_live_panel_reports_the_running_stage_agent_model_and_elapsed(self):
        tasks = _standard_tasks(architecture="passed", implementation="running")
        runs = [_run("r-1", "t-2", "running")]
        live = projection.build_live_panel(_workflow(), tasks, runs, now=NOW)
        self.assertTrue(live["running"])
        self.assertEqual(live["stage"], "IMPLEMENT")
        self.assertEqual(live["agent"], "opencode")
        self.assertEqual(live["agent_status"], "running")
        self.assertEqual(live["model"], "gpt-5-codex")
        self.assertEqual(live["elapsed_seconds"], 300.0)  # 12:30:00 -> 12:35:00
        self.assertEqual(live["next_stage"], "VERIFY")

    def test_live_panel_is_idle_when_nothing_is_running(self):
        live = projection.build_live_panel(
            _workflow("completed"), _standard_tasks(), now=NOW)
        self.assertFalse(live["running"])
        self.assertEqual(live["agent"], "")

    def test_live_panel_has_no_elapsed_time_without_a_recorded_start(self):
        tasks = _standard_tasks(implementation="running")
        runs = [_run("r-1", "t-2", "running", started_at=None)]
        live = projection.build_live_panel(_workflow(), tasks, runs, now=NOW)
        self.assertTrue(live["running"])
        self.assertIsNone(live["elapsed_seconds"])
        self.assertEqual(live["elapsed_text"], "-")

    def test_live_panel_uses_the_latest_run_not_a_stale_one(self):
        tasks = _standard_tasks(implementation="running")
        runs = [
            _run("r-old", "t-2", "failed", started_at="2026-10-03T12:05:00+00:00"),
            _run("r-new", "t-2", "running", started_at="2026-10-03T12:34:00+00:00"),
        ]
        live = projection.build_live_panel(_workflow(), tasks, runs, now=NOW)
        self.assertEqual(live["run_id"], "r-new")
        self.assertEqual(live["elapsed_seconds"], 60.0)

    def test_recent_activity_is_newest_first_and_labelled(self):
        events = [
            {"timestamp": "2026-10-03T12:10:00+00:00", "type": "task.started",
             "message": "implement started"},
            {"timestamp": "2026-10-03T12:34:00+00:00", "type": "verification.completed",
             "message": "checks finished"},
        ]
        activity = projection.recent_activity(events)
        self.assertEqual(len(activity), 2)
        self.assertIn("checks finished", activity[0]["text"])
        self.assertEqual(activity[0]["type"], "verification.completed")

    def test_recent_activity_respects_its_limit(self):
        events = [{"timestamp": f"2026-10-03T12:{minute:02d}:00+00:00",
                   "type": "note", "message": str(minute)} for minute in range(20)]
        self.assertEqual(len(projection.recent_activity(events, limit=5)), 5)

    # ------------------------------------------------------------------
    # verification
    # ------------------------------------------------------------------
    def test_verification_totals_sum_recorded_counts(self):
        runs = [
            _verification("v-1", "t-3", total_checks=21, passed_checks=18,
                          failed_checks=1, skipped_checks=2),
            _verification("v-2", "t-4", total_checks=3, passed_checks=3),
        ]
        totals = projection.verification_totals(runs)
        self.assertEqual(totals["passed"], 21)
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(totals["skipped"], 2)
        self.assertEqual(totals["total"], 24)
        self.assertEqual(totals["state"], "failed")
        self.assertIn("21 passed", totals["summary"])

    def test_check_rows_outrank_a_lagging_run_counter(self):
        checks = [{"name": "unit", "status": "passed"},
                  {"name": "lint", "status": "failed"},
                  {"name": "docs", "status": "skipped"}]
        runs = [_verification("v-1", "t-3", total_checks=99, passed_checks=99)]
        totals = projection.verification_totals(runs, checks)
        self.assertEqual((totals["passed"], totals["failed"], totals["skipped"]),
                         (1, 1, 1))
        self.assertEqual(totals["total"], 3)

    def test_no_verification_reads_as_pending_not_passed(self):
        self.assertEqual(projection.verification_totals([])["state"], "pending")

    # ------------------------------------------------------------------
    # failures
    # ------------------------------------------------------------------
    def test_failure_summary_exposes_the_actionable_fields_only(self):
        summary = projection.failure_summary(
            _failure("f-1", "t-3"), [_run("r-9", "t-3", "failed")], _standard_tasks())
        self.assertEqual(summary["category"], "test failure")
        self.assertEqual(summary["severity"], "high")
        self.assertIn("assert 1 == 2", summary["error"])
        self.assertTrue(summary["retryable"])
        self.assertTrue(summary["repairable"])
        self.assertEqual(summary["action"], "retry same agent")
        self.assertIn("verification work", summary["task_label"])
        # Raw persistence fields never reach the surface.
        self.assertNotIn("structured_evidence", summary)
        self.assertNotIn("workflow_id", summary)

    def test_failure_summary_falls_back_to_structured_evidence(self):
        failure = _failure("f-1", "t-3", evidence=None,
                           structured_evidence={"summary": "exit code 1 on lint"})
        self.assertEqual(projection.failure_summary(failure)["evidence"],
                         "exit code 1 on lint")

    def test_failure_summary_labels_an_unrelated_run_as_none(self):
        summary = projection.failure_summary(_failure("f-1", "t-3"), [], [])
        self.assertEqual(summary["run_label"], "")

    # ------------------------------------------------------------------
    # worktree and readiness
    # ------------------------------------------------------------------
    def test_worktree_summary_reports_provenance_and_changed_files(self):
        ref = {"path": "D:/repo/.agentops/worktrees/wf-1", "branch": "agentops/wf-1",
               "base_branch": "main", "base_commit": "abc1234"}
        runs = [_run("r-1", "t-2", "completed", files_changed=("a.py", "b.py"),
                     diff_stat="a.py | 2 +")]
        worktree = projection.worktree_summary(ref, runs)
        self.assertTrue(worktree["present"])
        self.assertTrue(worktree["managed"])
        self.assertEqual(worktree["branch"], "agentops/wf-1")
        self.assertEqual(worktree["base_branch"], "main")
        self.assertEqual(worktree["base_commit"], "abc1234")
        self.assertEqual(worktree["changed_count"], 2)
        self.assertEqual(worktree["diff_stat"], "a.py | 2 +")

    def test_worktree_summary_deduplicates_changed_files_across_runs(self):
        ref = {"path": "D:/repo/.agentops/worktrees/wf-1"}
        runs = [_run("r-1", "t-1", "completed", files_changed=("a.py",)),
                _run("r-2", "t-2", "completed", files_changed=("a.py", "b.py"))]
        self.assertEqual(projection.worktree_summary(ref, runs)["changed_files"],
                         ["a.py", "b.py"])

    def test_worktree_summary_is_empty_without_provenance(self):
        worktree = projection.worktree_summary(None, [])
        self.assertFalse(worktree["present"])
        self.assertFalse(worktree["managed"])
        self.assertEqual(worktree["changed_count"], 0)

    def test_merge_readiness_reports_ready_only_when_the_engine_says_so(self):
        ready = projection.merge_readiness({"ready": True, "reasons": []}, has_worktree=True)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["label"], "Ready to merge")
        blocked = projection.merge_readiness(
            {"ready": False, "reasons": ["no passed review task"]}, has_worktree=True)
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["reasons"], ["no passed review task"])
        self.assertEqual(blocked["label"], "Not ready to merge")

    def test_ready_without_a_worktree_is_still_blocked(self):
        result = projection.merge_readiness({"ready": True, "reasons": []}, has_worktree=False)
        self.assertFalse(result["ready"])
        self.assertIn("no worktree to merge from", result["reasons"])

    def test_missing_readiness_read_blocks_rather_than_claiming_ready(self):
        self.assertFalse(projection.merge_readiness(None, has_worktree=True)["ready"])

    # ------------------------------------------------------------------
    # cancellation
    # ------------------------------------------------------------------
    def _running_live(self) -> dict:
        return projection.build_live_panel(
            _workflow(), _standard_tasks(implementation="running"),
            [_run("r-1", "t-2", "running")], now=NOW)

    def test_cancellation_is_offered_only_while_running(self):
        running = projection.cancellation_state(self._running_live())
        self.assertTrue(running["cancellable"])
        self.assertEqual(running["state"], "running")
        idle = projection.cancellation_state(
            projection.build_live_panel(_workflow("completed"), _standard_tasks(), now=NOW))
        self.assertFalse(idle["cancellable"])
        self.assertEqual(idle["state"], "idle")

    def test_a_requested_cancel_locks_immediately_and_is_not_success(self):
        state = projection.cancellation_state(self._running_live(), cancel_requested=True)
        self.assertFalse(state["cancellable"])
        self.assertTrue(state["cancel_requested"])
        self.assertEqual(state["state"], "cancelling")
        self.assertEqual(state["label"], "Cancelling")
        self.assertFalse(state["cancelled"])

    def test_a_cancelled_workflow_is_never_reported_as_success(self):
        tasks = _standard_tasks(architecture="passed", implementation="cancelled")
        live = projection.build_live_panel(_workflow("cancelled"), tasks, now=NOW)
        state = projection.cancellation_state(live)
        self.assertTrue(state["cancelled"])
        self.assertEqual(state["state"], "cancelled")
        self.assertFalse(state["cancellable"])
        self.assertEqual(live["stages"][1]["state"], "cancelled")
        self.assertEqual(live["stages"][-1]["state"], "cancelled")

    def test_finished_stages_stay_passed_after_a_cancellation(self):
        tasks = _standard_tasks(architecture="passed", implementation="cancelled")
        stages = projection.build_stage_flow(_workflow("cancelled"), tasks)
        self.assertEqual(stages[0]["state"], "passed")
        self.assertEqual(stages[1]["state"], "cancelled")

    def test_an_operation_in_flight_makes_a_stopped_workflow_cancellable(self):
        # The shell may be running a task that has not persisted a run row yet.
        live = projection.build_live_panel(_workflow("pending"), _standard_tasks(), now=NOW)
        self.assertTrue(
            projection.cancellation_state(live, operation_active=True)["cancellable"])

    # ------------------------------------------------------------------
    # task and run detail
    # ------------------------------------------------------------------
    def test_task_detail_groups_what_the_task_owns(self):
        tasks = _standard_tasks(verification="passed")
        detail = projection.task_detail(
            tasks[2], [_run("r-1", "t-3", "completed")],
            [_verification("v-1", "t-3", overall_status="passed")],
            [_failure("f-1", "t-3")],
            [{"id": "a-1", "task_id": "t-3", "name": "report.md"}])
        self.assertEqual(detail["role"], "verification")
        self.assertEqual(detail["attempts"], "1 / 3")
        self.assertEqual(len(detail["runs"]), 1)
        self.assertEqual(len(detail["verifications"]), 1)
        self.assertEqual(len(detail["failures"]), 1)
        self.assertEqual(len(detail["artifacts"]), 1)
        self.assertEqual(detail["verification"], "passed")

    def test_task_detail_excludes_records_belonging_to_other_tasks(self):
        detail = projection.task_detail(
            _standard_tasks()[0], [_run("r-1", "t-2", "completed")], [], [], [])
        self.assertEqual(detail["runs"], [])

    def test_run_detail_carries_the_structured_result_and_diff(self):
        run = _run("r-1", "t-2", "completed", exit_code=0, duration_seconds=12.5,
                   ended_at="2026-10-03T12:30:12+00:00", files_changed=("a.py",),
                   diff_stat="a.py | 1 +", structured_result={"changed": ["a.py"]},
                   relationship="root")
        detail = projection.run_detail(run)
        self.assertEqual(detail["agent"], "opencode")
        self.assertEqual(detail["model"], "gpt-5-codex")
        self.assertEqual(detail["role"], "implementation")
        self.assertEqual(detail["exit_code"], 0)
        self.assertEqual(detail["duration_text"], "12.5s")
        self.assertEqual(detail["files_changed"], ["a.py"])
        self.assertEqual(detail["structured_result"], {"changed": ["a.py"]})
        self.assertIn("worktrees/wf-1", detail["worktree"])

    def test_run_detail_of_nothing_is_empty_not_invented(self):
        detail = projection.run_detail(None)
        self.assertEqual(detail["agent"], "")
        self.assertIsNone(detail["structured_result"])
        self.assertEqual(detail["files_changed"], [])
        self.assertEqual(detail["duration_text"], "-")

    # ------------------------------------------------------------------
    # tolerance
    # ------------------------------------------------------------------
    def test_malformed_records_are_dropped_rather_than_rendered(self):
        stages = projection.build_stage_flow(
            "not a dict", ["not a dict", None], [{"id": "r-1", "task_id": "t-1"}])
        self.assertEqual([stage["label"] for stage in stages], ["FINALIZE"])

    def test_projections_never_raise_on_missing_fields(self):
        for build in (
            lambda: projection.build_stage_flow({}, []),
            lambda: projection.build_live_panel({}, [], [], [], [], []),
            lambda: projection.verification_totals([{}]),
            lambda: projection.failure_summary({}),
            lambda: projection.worktree_summary({}, [], {}),
            lambda: projection.cancellation_state({}),
            lambda: projection.task_detail({}),
            lambda: projection.run_detail({}),
            lambda: projection.recent_activity(None),
            lambda: projection.current_stage(None),
            lambda: projection.next_stage(None),
            lambda: projection.is_custom_dag(None),
        ):
            with self.subTest(build=build):
                build()


def _grid_text(grid) -> str:
    """All rendered label text of a KeyValueGrid, for readable assertions."""
    from PySide6.QtWidgets import QLabel

    return " | ".join(label.text() for label in grid.findChildren(QLabel))


@requires_qt
class ControlCenterViewTests(unittest.TestCase):
    """The widget layer: what the operator actually sees and can click."""

    def setUp(self):
        from agentops.gui.settings import AppSettings
        from agentops.gui.shell import MainWindow

        qt_app()
        self.controller = FakeController()
        settings = AppSettings()
        settings.touch_repository(REPOSITORY)
        self.window = MainWindow(self.controller, settings=settings)
        self.addCleanup(self._teardown)
        self.window.show()
        wait(50)
        self.view = self.window._views["workflows"]

    def _teardown(self):
        from PySide6.QtWidgets import QMessageBox

        with patch("agentops.gui.shell.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            destroy(self.window)

    def _select(self, table, item_id: str) -> None:
        """Select the row carrying ``item_id``; tables sort, indexes do not."""
        proxy = table.proxy
        for index in range(proxy.rowCount()):
            if str((table.row_of(proxy.index(index, 0)) or {}).get("id")) == item_id:
                table.select_row_index(index)
                return
        self.fail(f"no row for {item_id}")

    def _activate(self) -> None:
        self.window.navigate("workflows")
        self.assertTrue(wait_until(lambda: self.view._current_id == "wf-1"))
        self.assertTrue(wait_until(lambda: self.view._flow.stage_keys()))

    # ------------------------------------------------------------------
    def test_active_workflow_display_shows_stages_and_the_running_stage(self):
        self._activate()
        self.assertEqual(
            self.view._flow.stage_keys(),
            ["architecture", "implementation", "verification", "review", "finalize"],
        )
        self.assertEqual(self.view._flow.stage_states()[1], "running")
        self.assertEqual(self.view._live._stage_label.text(), "IMPLEMENT")
        self.assertEqual(self.view._live._agent.text(), "codex")
        self.assertEqual(self.view._live._status.text(), "RUNNING")
        self.assertIn("Model", self.view._live._fact_labels["model"].text())

    def test_stage_transitions_are_reflected_on_refresh(self):
        self._activate()
        self.view._payload["tasks"] = [
            {**self.view._payload["tasks"][0], "status": "passed",
             "finished_at": "2026-10-03T12:20:00Z"},
            {**self.view._payload["tasks"][1], "status": "running"},
        ]
        self.view._payload["workflow"] = {**self.view._payload["workflow"],
                                         "status": "running"}
        self.view._on_payload({
            "payload": {"workflow": self.view._payload["workflow"],
                        "tasks": self.view._payload["tasks"],
                        "runs": self.view._payload["runs"],
                        "verifications": self.view._payload["verifications"],
                        "failures": self.view._payload["failures"],
                        "worktree_ref": {"path": "D:/repo/.agentops/worktrees/wf-1",
                                         "branch": "agentops/wf-1",
                                         "base_branch": "main", "base_commit": "1234567"}},
            "artifacts": [], "events": [], "readiness": {"ready": False, "reasons": []},
        })
        self.assertEqual(self.view._flow.stage_states()[0], "passed")
        self.assertEqual(self.view._live._stage_label.text(), "IMPLEMENT")

    def test_task_selection_shows_description_role_attempts_and_relations(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._tasks.proxy.rowCount() == 4))
        self._select(self.view._tasks, "t-2")
        self.assertTrue(wait_until(lambda: self.view._selected_task_id == "t-2"))
        rendered = _grid_text(self.view._task_detail)
        self.assertIn("do the work", rendered)
        self.assertIn("implementation", rendered)
        self.assertIn("1 / 3", rendered)
        self.assertIn("t-1", rendered)

    def test_run_selection_shows_agent_model_exit_and_diff(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._runs.proxy.rowCount() == 3))
        # r-1 is the completed implementation run: exit code, duration, diff.
        self._select(self.view._runs, "r-1")
        self.assertTrue(wait_until(
            lambda: "gpt-5-codex" in _grid_text(self.view._run_detail)))
        rendered = _grid_text(self.view._run_detail)
        self.assertIn("codex", rendered)
        self.assertIn("implementation", rendered)
        self.assertIn("12.5s", rendered)
        self.assertIn("worktrees/wf-1", rendered)
        self.assertIn("src/app.py", rendered)

    def test_verification_display_counts_outcomes_and_opens_checks(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._verifications.proxy.rowCount() == 2))
        self.assertEqual(self.view._tally.text_for("Checks passed"), "3")
        self.assertEqual(self.view._tally.text_for("Checks failed"), "1")
        self._select(self.view._verifications, "v-1")
        self.assertTrue(wait_until(lambda: self.view._checks.proxy.rowCount() == 1))
        record = self.view._checks.row_of(self.view._checks.proxy.index(0, 0))
        self.assertEqual(record["name"], "unit")

    def test_failure_display_shows_category_severity_and_recommended_action(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._failures.proxy.rowCount() == 1))
        self._select(self.view._failures, "f-1")
        self.assertTrue(wait_until(
            lambda: "test failure" in _grid_text(self.view._failure_grid)))
        rendered = _grid_text(self.view._failure_grid)
        self.assertIn("high", rendered)
        self.assertIn("retry same agent", rendered)
        self.assertIn("yes", rendered)
        self.assertIn("do the work", rendered)

    def test_cancellation_is_offered_while_running_and_locks_on_request(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._cancel.isEnabled()))
        self.view._cancel.click()
        self.assertTrue(self.controller.cancelled)
        # Immediate, before any round trip: the button locks and says so.
        self.assertFalse(self.view._cancel.isEnabled())
        self.assertEqual(self.view._cancel.text(), "Cancelling")
        self.assertEqual(self.view._status_badge.text(), "CANCELLING")

    def test_conflicting_actions_are_disabled_while_a_run_is_live(self):
        self._activate()
        self.assertTrue(wait_until(lambda: self.view._cancel.isEnabled()))
        self.assertFalse(self.view._merge.isEnabled())
        self.assertFalse(self.view._cleanup.isEnabled())

    def test_a_finished_workflow_reports_blocked_readiness_with_reasons(self):
        self._activate()
        rendered = _grid_text(self.view._worktree_grid)
        self.assertIn("agentops/wf-1", rendered)
        self.assertIn("main", rendered)
        self.assertIn("1234567", rendered)
        self.assertIn("Not ready to merge", rendered)
        self.assertIn("no passed review task", rendered)
        self.assertEqual(self.view._readiness.text_for("Ready to merge"), "0")

    def test_worktree_actions_are_offered_when_nothing_is_running(self):
        self._activate()
        self.view._cancel_requested = False
        self.view._payload["workflow"] = {**self.view._payload["workflow"],
                                          "status": "completed"}
        self.view._payload["tasks"] = [dict(task, status="passed")
                                       for task in self.view._payload["tasks"]]
        self.view._payload["runs"] = []
        self.view._apply_state(
            projection.cancellation_state(
                projection.build_live_panel(self.view._payload["workflow"],
                                            self.view._payload["tasks"])),
            self.view._payload["workflow"])
        self.view._apply_worktree({"ready": True, "reasons": []})
        self.assertTrue(self.view._merge.isEnabled())
        self.assertTrue(self.view._cleanup.isEnabled())
        self.assertEqual(self.view._readiness.text_for("Ready to merge"), "1")
        self.assertIn("Ready to merge", _grid_text(self.view._worktree_grid))

    def test_selecting_a_stage_highlights_it_and_reaches_its_task(self):
        self._activate()
        stage = self.view._flow._stages[1]
        self.view._select_stage(stage)
        self.assertEqual(self.view._selected_task_id, stage["task_ids"][0])
        self.assertEqual(self.view._tabs.currentIndex(), 0)

    def test_no_repository_leaves_an_explicit_empty_state(self):
        self.window._repository = ""
        self.view.refresh()
        self.assertEqual(self.view._list._empty._heading.text(),
                         "No repository selected")

    def test_deep_link_to_a_task_selects_it_after_the_payload_arrives(self):
        self.window.open_task("wf-1", "t-2")
        self.assertTrue(wait_until(lambda: self.view._selected_task_id == "t-2"))


    def test_the_pipeline_column_holds_every_stage_without_a_scrollbar(self):
        # The stage flow is the mission's main artifact: it must fit the pane
        # the shell gives it, or the whole surface collapses into scrolling.
        self._activate()
        flow = self.view._flow
        wait(50)
        self.assertGreaterEqual(flow.sizeHint().height(), self.view._flow.height())
        for key in self.view._flow.stage_keys():
            self.assertIsNotNone(self.view._flow.node_for(key))

    def test_stage_nodes_carry_their_facts_as_readable_text(self):
        self._activate()
        node = self.view._flow.node_for("implementation")
        self.assertIsNotNone(node)
        self.assertIn("codex", node.fact_labels["agent"].text())
        self.assertIn("gpt-5-codex", node.fact_labels["model"].text())
        self.assertEqual(node.state.text(), "RUNNING")

    def test_an_unknown_stage_fact_is_blank_not_a_placeholder_word(self):
        self._activate()
        node = self.view._flow.node_for("review")
        self.assertIsNotNone(node)
        # t-4 has no agent, no run, no duration: those lines stay empty rather
        # than claiming values nobody recorded.
        self.assertEqual(node.fact_labels["agent"].text(), "")
        self.assertEqual(node.fact_labels["duration"].text(), "")

    def test_the_live_card_leads_with_the_current_stage(self):
        self._activate()
        self.assertEqual(self.view._live._stage_label.text(), "IMPLEMENT")
        self.assertIn("VERIFY", self.view._live._fact_labels["next"].text())


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
