"""Characterization tests for the ``agentops.gui_controller`` facade.

These pin the controller's observable contract -- the import surface, the
exact key order of every serializer payload, and the shape of the public read
methods that had no direct coverage -- so a structural refactor of the module
can be proven behaviour-neutral. They use only public names and real
``StateStore`` / ``LogManager`` / ``ArtifactStore`` instances in a temporary,
non-git directory; the one collaborator replaced is ``GitWorktreeManager``,
patched at ``agentops.gui_controller.GitWorktreeManager`` exactly as the
existing GUI operation tests do, so the patch target itself is covered too.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import agentops.gui_controller as gui_controller
from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.artifacts import ArtifactKind, ArtifactStore
from agentops.config import AppConfig
from agentops.events import Event, EventSeverity, EventType
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)
from agentops.git import GitError, WorktreeRef
from agentops.gui_controller import (
    AgentOpsController,
    serialize_agent_run,
    serialize_failure,
    serialize_task,
    serialize_verification_check,
    serialize_verification_report,
    serialize_verification_run,
    serialize_workflow,
    serialize_worktree_ref,
)
from agentops.logging import LogManager
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationCheckStatus,
    VerificationProfile,
    VerificationProfileMode,
    VerificationReport,
    VerificationReportStatus,
    VerificationRunStatus,
)

# -- pinned contracts --------------------------------------------------------
# Key ORDER matters: these dicts are rendered by the GUI and written as JSON,
# so the lists below are compared with assertEqual, not as sets.

FAILURE_KEYS = [
    "id",
    "workflow_id",
    "task_id",
    "agent_run_id",
    "source",
    "category",
    "severity",
    "retryable",
    "repairable",
    "evidence",
    "primary_error",
    "verification_run_id",
    "recommended_action",
    "attempt",
    "repair_cycle",
    "recovery_state",
    "structured_evidence",
    "created_at",
    "updated_at",
]
TASK_KEYS = [
    "id",
    "workflow_id",
    "description",
    "role",
    "assigned_agent",
    "dependencies",
    "status",
    "attempts",
    "max_attempts",
    "result",
    "verified",
    "verification_run_id",
    "review_verdict",
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
]
WORKFLOW_KEYS = ["id", "description", "status", "created_at", "updated_at"]
WORKTREE_REF_KEYS = [
    "id",
    "workflow_id",
    "path",
    "branch",
    "base_branch",
    "base_commit",
    "created_at",
]
VERIFICATION_CHECK_KEYS = [
    "id",
    "run_id",
    "workflow_id",
    "task_id",
    "profile_name",
    "name",
    "check_class",
    "command",
    "working_directory",
    "timeout_seconds",
    "required",
    "policy",
    "status",
    "exit_code",
    "started_at",
    "ended_at",
    "duration_seconds",
    "stdout_path",
    "stderr_path",
    "failure_reason",
    "created_at",
    "updated_at",
]
VERIFICATION_RUN_KEYS = [
    "id",
    "workflow_id",
    "task_id",
    "profile_name",
    "profile_snapshot",
    "mode",
    "concurrency",
    "status",
    "source_agent_run_id",
    "started_at",
    "ended_at",
    "duration_seconds",
    "total_checks",
    "passed_checks",
    "failed_checks",
    "skipped_checks",
    "required_failures",
    "overall_status",
    "created_at",
    "updated_at",
]
VERIFICATION_REPORT_KEYS = [
    "id",
    "run_id",
    "workflow_id",
    "task_id",
    "profile_name",
    "total_checks",
    "passed_checks",
    "failed_checks",
    "skipped_checks",
    "required_failures",
    "duration_seconds",
    "overall_status",
    "generated_at",
    "transcript",
]
AGENT_RUN_KEYS = [
    "id",
    "workflow_id",
    "task_id",
    "attempt",
    "agent",
    "executable",
    "role",
    "model",
    "started_at",
    "ended_at",
    "duration_seconds",
    "status",
    "exit_code",
    "cancelled",
    "timed_out",
    "terminated",
    "command",
    "command_metadata",
    "working_directory",
    "worktree",
    "prompt_metadata",
    "stdout_path",
    "stderr_path",
    "log_path",
    "files_changed",
    "diff_stat",
    "error",
    "failure_classification",
    "relationship",
    "parent_run_id",
    "retry_of",
    "repair_of",
    "structured_result",
    "created_at",
    "updated_at",
]

DASHBOARD_KEYS = [
    "repository",
    "total_workflows",
    "active_workflows",
    "task_counts",
    "recent_workflows",
    "latest_workflow_id",
    "verification_status",
    "recent_verifications",
    "failure_count",
    "needs_attention",
    "recent_failures",
    "recent_events",
]

# Every public name the single-file module exposed (imports included, since
# tests and callers patch or import them from here). New names may be added.
MODULE_PUBLIC_NAMES = [
    "AgentOpsController",
    "AgentRegistry",
    "AgentRun",
    "AgentRunStatus",
    "AgentRunner",
    "AppConfig",
    "ArtifactError",
    "ArtifactStore",
    "Callable",
    "EventCallback",
    "Failure",
    "GitError",
    "GitRunMetadataCollector",
    "GitWorktreeManager",
    "LogManager",
    "OperationCancelled",
    "Path",
    "STANDARD_TASK_ROLES",
    "StateStore",
    "Task",
    "VerificationCheck",
    "VerificationKernel",
    "VerificationReport",
    "VerificationRun",
    "Verifier",
    "Workflow",
    "WorkflowEngine",
    "Worktree",
    "WorktreeRef",
    "annotations",
    "assess_workflow_readiness",
    "asyncio",
    "dataclass",
    "field",
    "finalize_for_outcome",
    "implementation_passed",
    "load_config",
    "record_worktree_provenance",
    "replace",
    "serialize_agent_run",
    "serialize_failure",
    "serialize_task",
    "serialize_verification_check",
    "serialize_verification_report",
    "serialize_verification_run",
    "serialize_workflow",
    "serialize_worktree_ref",
    "suppress",
    "threading",
    "verdict_summary_for_task",
]
CONTROLLER_PUBLIC_METHODS = [
    "TASK_ROLES",
    "cancel",
    "cleanup_worktree",
    "config",
    "config_path",
    "dashboard_summary",
    "delete_agent_run",
    "delete_artifact",
    "delete_failure",
    "delete_task",
    "delete_verification_run",
    "delete_workflow",
    "detect_agents",
    "get_agent_run",
    "get_verification_run",
    "get_workflow",
    "get_worktree_ref",
    "inspect_worktree",
    "interrupted_work",
    "latest_workflow",
    "list_agent_profiles",
    "list_agent_runs",
    "list_artifacts",
    "list_failures",
    "list_logs",
    "list_recent_agent_runs",
    "list_recent_failures",
    "list_recent_verification_runs",
    "list_tasks",
    "list_verification_runs",
    "list_workflows",
    "list_worktree_refs",
    "list_worktrees",
    "query_events",
    "read_artifact",
    "read_log",
    "recategorize_task",
    "recover_interrupted",
    "rename_task",
    "rename_workflow",
    "retry_merge",
    "run_agent",
    "run_task",
    "state_path",
    "task_options",
    "workflow_diff",
    "workflow_readiness",
]


def _config() -> AppConfig:
    return AppConfig({}, {}, (), max_attempts=1, concurrency=1)


def _profile() -> VerificationProfile:
    return VerificationProfile(
        name="unit",
        mode=VerificationProfileMode.FAIL_FAST,
        concurrency=1,
        default_timeout_seconds=300,
        checks=(VerificationCheckSpec("unit", VerificationCheckClass.TESTS, ("unit",)),),
    )


class _Seeded:
    """A temp directory whose default controller state is fully populated."""

    def __init__(self, root: Path):
        self.root = root
        self.controller = AgentOpsController(config=_config())
        state = StateStore(self.controller._state_path(root))
        try:
            self.workflow_id = state.create_workflow("ship the feature")
            first = state.add_task(Task("plan the change", "architecture", self.workflow_id))
            first.status = TaskStatus.PASSED
            state.update_task(first)
            second = state.add_task(Task("write the code", "implementation", self.workflow_id))
            self.task_ids = [first.id, second.id]
            self.task_id = first.id

            run = state.create_agent_run(
                AgentRunContext(agent="demo", workflow_id=self.workflow_id, task_id=self.task_id)
            )
            state.start_agent_run(run.id)
            state.mark_agent_run_running(run.id)
            state.finish_agent_run(
                run.id,
                AgentRunOutcome(status=AgentRunStatus.COMPLETED, exit_code=0, duration_seconds=1.5),
            )
            self.agent_run_id = run.id

            vrun = state.create_verification_run(self.workflow_id, self.task_id, _profile())
            state.start_verification_run(vrun.id)
            check = state.create_verification_check(
                vrun.id,
                self.workflow_id,
                self.task_id,
                "unit",
                "unit",
                "tests",
                ("unit",),
                None,
                300,
                True,
                "sequential",
            )
            state.finish_verification_check(check.id, VerificationCheckStatus.PASSED, exit_code=0)
            state.finish_verification_run(
                vrun.id,
                VerificationRunStatus.COMPLETED,
                VerificationReportStatus.PASSED,
                total_checks=1,
                passed_checks=1,
                failed_checks=0,
                skipped_checks=0,
                required_failures=0,
                duration_seconds=0.5,
            )
            state.create_verification_report(
                VerificationReport(
                    id="report-1",
                    run_id=vrun.id,
                    workflow_id=self.workflow_id,
                    task_id=self.task_id,
                    profile_name="unit",
                    total_checks=1,
                    passed_checks=1,
                    duration_seconds=0.5,
                    overall_status=VerificationReportStatus.PASSED,
                )
            )
            self.verification_run_id = vrun.id

            state.create_failure(
                Failure(
                    id="failure-1",
                    workflow_id=self.workflow_id,
                    task_id=self.task_id,
                    agent_run_id=None,
                    source=FailureSource.AGENT,
                    category=FailureCategory.AGENT_ERROR,
                    severity=FailureSeverity.HIGH,
                    retryable=True,
                    repairable=True,
                    evidence="boom",
                    recommended_action=RepairAction.RETRY_SAME_AGENT,
                    recovery_state=RecoveryState.RECOVERED_FAILED,
                )
            )
            state.record_typed_event(
                Event(
                    workflow_id=self.workflow_id,
                    task_id=self.task_id,
                    type=EventType.TASK_COMPLETED,
                    severity=EventSeverity.INFO,
                    message="task completed",
                )
            )
            store = ArtifactStore(self.controller._artifact_root(root))
            artifact = store.write(
                "the plan",
                kind=ArtifactKind.PLANS,
                name="plan.md",
                workflow_id=self.workflow_id,
                task_id=self.task_id,
            )
            state.create_artifact_record(artifact)
            self.artifact_id = artifact.id
            state.record_worktree_ref(
                WorktreeRef(
                    id="ref-1",
                    workflow_id=self.workflow_id,
                    path=str(root / "wt"),
                    branch="agentops/x",
                    base_branch="main",
                    base_commit="deadbeef",
                )
            )
        finally:
            state.close()
        self.log_path = LogManager(root / ".agentops" / "logs").write_run(
            self.task_id, "demo", "hello stdout", "", "meta"
        )


class ImportSurfaceTests(unittest.TestCase):
    def test_module_exposes_the_same_public_names(self):
        missing = [name for name in MODULE_PUBLIC_NAMES if not hasattr(gui_controller, name)]
        self.assertEqual(missing, [])

    def test_controller_public_methods_are_unchanged(self):
        methods = sorted(
            name for name, _ in inspect.getmembers(AgentOpsController) if not name.startswith("_")
        )
        self.assertEqual(methods, CONTROLLER_PUBLIC_METHODS)

    def test_controller_is_a_dataclass_with_the_same_init(self):
        self.assertEqual(
            list(inspect.signature(AgentOpsController).parameters),
            ["config_path", "state_path", "config"],
        )
        self.assertEqual(
            AgentOpsController.__module__.split(".")[:2], ["agentops", "gui_controller"]
        )

    def test_task_roles_constant(self):
        self.assertEqual(
            AgentOpsController.TASK_ROLES,
            ("architecture", "implementation", "verification", "review", "debugging"),
        )


class SerializerKeyOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seed = _Seeded(Path(self.tmp.name))
        self.state = StateStore(self.seed.controller._state_path(self.seed.root))

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_failure(self):
        failure = self.state.list_failures(self.seed.workflow_id)[0]
        payload = serialize_failure(failure)
        self.assertEqual(list(payload), FAILURE_KEYS)
        self.assertEqual(payload["source"], FailureSource.AGENT.value)
        self.assertEqual(payload["recovery_state"], RecoveryState.RECOVERED_FAILED.value)
        self.assertIsNone(payload["structured_evidence"])

    def test_task(self):
        task = self.state.list_tasks(self.seed.workflow_id)[0]
        payload = serialize_task(task)
        self.assertEqual(list(payload), TASK_KEYS)
        self.assertEqual(payload["status"], "passed")
        self.assertIsInstance(payload["dependencies"], list)
        self.assertIsNone(payload["review_verdict"])

    def test_review_task_carries_a_verdict_summary(self):
        payload = serialize_task(Task("review it", "review", "w1"))
        self.assertEqual(payload["review_verdict"]["verdict"], "missing")

    def test_workflow(self):
        payload = serialize_workflow(self.state.get_workflow(self.seed.workflow_id))
        self.assertEqual(list(payload), WORKFLOW_KEYS)

    def test_worktree_ref(self):
        payload = serialize_worktree_ref(self.state.get_worktree_ref(self.seed.workflow_id))
        self.assertEqual(list(payload), WORKTREE_REF_KEYS)

    def test_verification_run_check_and_report(self):
        run = self.state.get_verification_run(self.seed.verification_run_id)
        self.assertEqual(list(serialize_verification_run(run)), VERIFICATION_RUN_KEYS)
        checks = self.state.list_verification_checks(run.id)
        self.assertEqual(list(serialize_verification_check(checks[0])), VERIFICATION_CHECK_KEYS)
        report = self.state.get_verification_report_by_run(run.id)
        self.assertEqual(list(serialize_verification_report(report)), VERIFICATION_REPORT_KEYS)

    def test_agent_run(self):
        run = self.state.get_agent_run(self.seed.agent_run_id)
        payload = serialize_agent_run(run)
        self.assertEqual(list(payload), AGENT_RUN_KEYS)
        self.assertEqual(payload["status"], "completed")


class ControllerReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.seed = _Seeded(self.root)
        self.controller = self.seed.controller

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_derived_reads(self):
        self.assertEqual(self.controller.list_agent_profiles(), [])
        self.assertIsInstance(self.controller.detect_agents(), dict)
        self.assertEqual(
            self.controller.task_options(),
            {
                "routing_enabled": bool(_config().routing_enabled),
                "verification_profiles": sorted(_config().verification_profiles),
                "default_verification_profile": _config().default_verification_profile,
            },
        )

    def test_dashboard_summary(self):
        summary = self.controller.dashboard_summary(self.root)
        self.assertEqual(list(summary), DASHBOARD_KEYS)
        self.assertEqual(summary["repository"], self.root.as_posix())
        self.assertEqual(summary["total_workflows"], 1)
        self.assertEqual(summary["active_workflows"], {"pending": 1, "running": 0})
        self.assertEqual(summary["task_counts"], {"passed": 1, "pending": 1})
        self.assertEqual(summary["latest_workflow_id"], self.seed.workflow_id)
        self.assertEqual(summary["verification_status"], {"passed": 1})
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual([f["id"] for f in summary["needs_attention"]], ["failure-1"])
        self.assertEqual(summary["recent_events"][0]["message"], "task completed")

    def test_dashboard_summary_on_an_empty_store(self):
        with tempfile.TemporaryDirectory() as empty:
            summary = AgentOpsController(config=_config()).dashboard_summary(empty)
        self.assertEqual(list(summary), DASHBOARD_KEYS)
        self.assertIsNone(summary["latest_workflow_id"])
        self.assertEqual(summary["recent_events"], [])

    def _non_actionable_failure(self, failure_id, workflow_id, task_id):
        """A HIGH-severity environment failure the system cannot self-repair."""
        return Failure(
            id=failure_id,
            workflow_id=workflow_id,
            task_id=task_id,
            agent_run_id=None,
            source=FailureSource.ENVIRONMENT,
            category=FailureCategory.ENVIRONMENT_FAILURE,
            severity=FailureSeverity.HIGH,
            retryable=False,
            repairable=False,
            evidence="no installed agent supports the role",
            recommended_action=RepairAction.REQUEST_APPROVAL,
            recovery_state=None,
        )

    def test_dashboard_summary_attention_includes_failures_from_failed_workflows(self):
        """A failed workflow's failures need attention even when not retryable."""
        state = StateStore(self.controller._state_path(self.root))
        try:
            failed_workflow_id = state.create_workflow("add a hello function")
            task = state.add_task(
                Task("plan the change", "architecture", failed_workflow_id)
            )
            task.status = TaskStatus.FAILED
            state.update_task(task)
            state.refresh_workflow_status(failed_workflow_id)
            state.create_failure(
                self._non_actionable_failure(
                    "failure-failed-workflow", failed_workflow_id, task.id
                )
            )
        finally:
            state.close()

        summary = self.controller.dashboard_summary(self.root)
        attention_ids = [f["id"] for f in summary["needs_attention"]]
        self.assertIn("failure-failed-workflow", attention_ids)

    def test_dashboard_summary_attention_excludes_non_actionable_failures(self):
        """The same failure on a workflow that did not fail stays out."""
        state = StateStore(self.controller._state_path(self.root))
        try:
            state.create_failure(
                self._non_actionable_failure(
                    "failure-transient", self.seed.workflow_id, self.seed.task_id
                )
            )
        finally:
            state.close()

        summary = self.controller.dashboard_summary(self.root)
        attention_ids = [f["id"] for f in summary["needs_attention"]]
        self.assertNotIn("failure-transient", attention_ids)
        self.assertEqual(attention_ids, ["failure-1"])

    def test_list_tasks_newest_first_with_filters_and_paging(self):
        tasks = self.controller.list_tasks(self.root)
        self.assertEqual([t["id"] for t in tasks], list(reversed(self.seed.task_ids)))
        passed = self.controller.list_tasks(self.root, status="passed")
        self.assertEqual([t["id"] for t in passed], [self.seed.task_id])
        paged = self.controller.list_tasks(self.root, limit=1, offset=1)
        self.assertEqual([t["id"] for t in paged], [self.seed.task_ids[0]])
        for bad in ({"limit": -1}, {"offset": -1}, {"limit": 1.5}):
            with self.assertRaises(ValueError):
                self.controller.list_tasks(self.root, **bad)

    def test_get_agent_run_known_and_unknown(self):
        run = self.controller.get_agent_run(self.root, self.seed.agent_run_id)
        self.assertEqual(run["id"], self.seed.agent_run_id)
        self.assertIsNone(self.controller.get_agent_run(self.root, "no-such-run"))

    def test_list_failures(self):
        failures = self.controller.list_failures(self.root, self.seed.workflow_id)
        self.assertEqual([f["id"] for f in failures], ["failure-1"])
        self.assertEqual(self.controller.list_failures(self.root, "other-workflow"), [])

    def test_query_events(self):
        events = self.controller.query_events(self.root, workflow_id=self.seed.workflow_id)
        self.assertIn("task completed", [e["message"] for e in events])
        self.assertEqual(self.controller.query_events(self.root, workflow_id="nope"), [])

    def test_artifacts_list_and_read(self):
        listed = self.controller.list_artifacts(self.root, workflow_id=self.seed.workflow_id)
        self.assertEqual([a["id"] for a in listed], [self.seed.artifact_id])
        read = self.controller.read_artifact(self.root, self.seed.artifact_id)
        self.assertEqual(list(read), ["artifact", "text"])
        self.assertEqual(read["text"], "the plan")
        with self.assertRaisesRegex(ValueError, "Unknown artifact"):
            self.controller.read_artifact(self.root, "missing")

    def test_logs_list_and_read(self):
        entries = self.controller.list_logs(self.root)
        self.assertTrue(entries)
        self.assertEqual(list(entries[0]), ["path", "name", "size", "modified"])
        names = [entry["name"] for entry in entries]
        self.assertTrue(all(not Path(name).is_absolute() for name in names))
        self.assertEqual(self.controller.list_logs(self.root, task_id="no-such-task"), [])
        with self.assertRaises(ValueError):
            self.controller.list_logs(self.root, limit=0)
        read = self.controller.read_log(self.root, names[0])
        self.assertEqual(list(read), ["name", "path", "truncated", "text"])
        self.assertFalse(read["truncated"])

    def test_workflow_readiness(self):
        readiness = self.controller.workflow_readiness(self.root, self.seed.workflow_id)
        self.assertEqual(
            list(readiness),
            ["workflow_id", "ready", "reasons", "verification_ok", "review_ok", "evidence_present"],
        )
        self.assertEqual(readiness["workflow_id"], self.seed.workflow_id)
        self.assertFalse(readiness["ready"])
        self.assertIsInstance(readiness["reasons"], list)

    def test_worktree_refs(self):
        ref = self.controller.get_worktree_ref(self.root, self.seed.workflow_id)
        self.assertEqual(ref["id"], "ref-1")
        self.assertIsNone(self.controller.get_worktree_ref(self.root, "nope"))
        refs = self.controller.list_worktree_refs(self.root)
        self.assertEqual([r["id"] for r in refs], ["ref-1"])

    def test_latest_workflow(self):
        latest = self.controller.latest_workflow(self.root)
        self.assertEqual(latest["id"], self.seed.workflow_id)
        self.assertEqual(
            list(latest), WORKFLOW_KEYS + ["tasks", "runs", "verifications", "worktree_ref"]
        )
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(AgentOpsController(config=_config()).latest_workflow(empty))


class WorktreeFacadeTests(unittest.TestCase):
    """The worktree reads delegate to GitWorktreeManager, patched at its
    ``agentops.gui_controller`` path; the patch must reach the controller."""

    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / "agentops-char-root"
        self.manager = MagicMock(name="GitWorktreeManager()")
        self.manager.repository_root.return_value = self.root
        self.controller = AgentOpsController(config=_config())

    def test_list_worktrees_maps_status_failures(self):
        good = MagicMock(
            repository=self.root, path=self.root / "a", branch="b1", head="h1", managed=True
        )
        bad = MagicMock(
            repository=self.root, path=self.root / "b", branch="b2", head="h2", managed=False
        )
        self.manager.list_worktrees.return_value = [good, bad]
        self.manager.worktree_status.side_effect = ["clean", GitError("boom")]
        with patch("agentops.gui_controller.GitWorktreeManager", return_value=self.manager):
            entries = self.controller.list_worktrees(self.root)
        self.manager.list_worktrees.assert_called_once_with(self.root)
        self.assertEqual(
            list(entries[0]), ["repository", "path", "branch", "head", "managed", "status"]
        )
        self.assertEqual([e["status"] for e in entries], ["clean", "(status unavailable)"])
        self.assertEqual(entries[1]["path"], str(self.root / "b"))

    def test_inspect_and_cleanup_delegate(self):
        self.manager.inspect_worktree.return_value = {"inspected": True}
        self.manager.cleanup_worktree.return_value = {"removed": True}
        with patch("agentops.gui_controller.GitWorktreeManager", return_value=self.manager):
            self.assertEqual(self.controller.inspect_worktree(self.root, "wt"), {"inspected": True})
            self.assertEqual(
                self.controller.cleanup_worktree(self.root, "wt", True), {"removed": True}
            )
        self.manager.inspect_worktree.assert_called_once_with(self.root, "wt")
        self.manager.cleanup_worktree.assert_called_once_with(self.root, "wt", True)

    def test_operation_root_uses_the_patched_manager_and_caches(self):
        with patch(
            "agentops.gui_controller.GitWorktreeManager", return_value=self.manager
        ) as manager_cls:
            self.assertEqual(self.controller._operation_root("somewhere"), self.root)
            self.assertEqual(self.controller._operation_root("somewhere"), self.root)
        self.assertEqual(manager_cls.call_count, 1)

    def test_operation_root_falls_back_without_caching_failures(self):
        self.manager.repository_root.side_effect = GitError("not a repo")
        with patch(
            "agentops.gui_controller.GitWorktreeManager", return_value=self.manager
        ) as manager_cls:
            self.assertEqual(self.controller._operation_root("plain"), Path("plain"))
            self.assertEqual(self.controller._operation_root("plain"), Path("plain"))
        self.assertEqual(manager_cls.call_count, 2)


class ModuleNamespacePatchTests(unittest.TestCase):
    """Every collaborator the existing tests patch on ``agentops.gui_controller``
    must still take effect when replaced on that module."""

    PATCHED_NAMES = (
        "GitWorktreeManager",
        "WorkflowEngine",
        "AgentRegistry",
        "AgentRunner",
        "Verifier",
        "VerificationKernel",
        "StateStore",
        "LogManager",
        "ArtifactStore",
        "load_config",
        "assess_workflow_readiness",
    )

    def test_names_resolve_on_the_module(self):
        for name in self.PATCHED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(gui_controller, name))
        self.assertTrue(hasattr(gui_controller.asyncio, "run"))

    def test_patched_state_store_reaches_a_read(self):
        sentinel = MagicMock(name="state")
        sentinel.list_worktree_refs.return_value = []
        with patch("agentops.gui_controller.StateStore", return_value=sentinel) as cls:
            self.assertEqual(
                AgentOpsController(state_path=Path("x.sqlite")).list_worktree_refs("."), []
            )
        cls.assert_called_once_with(Path("x.sqlite"))
        sentinel.close.assert_called_once_with()

    def test_patched_load_config_reaches_task_options(self):
        with patch("agentops.gui_controller.load_config", return_value=_config()) as loader:
            AgentOpsController().task_options()
        loader.assert_called_once_with(None)

    def test_patched_readiness_reaches_workflow_readiness(self):
        fake = MagicMock(
            workflow_id="w",
            ready=True,
            reasons=(),
            verification_ok=True,
            review_ok=True,
            evidence_present=True,
        )
        state = MagicMock(name="state")
        with (
            patch("agentops.gui_controller.StateStore", return_value=state),
            patch("agentops.gui_controller.assess_workflow_readiness", return_value=fake),
        ):
            result = AgentOpsController(state_path=Path("x.sqlite")).workflow_readiness(".", "w")
        self.assertTrue(result["ready"])

    def test_patched_log_manager_reaches_read_log(self):
        logs = MagicMock(name="logs")
        logs.read_tail.return_value = ("tail", True)
        logs.root = Path("/logs")
        controller = AgentOpsController(config=_config())
        controller._operation_root = lambda directory: Path(directory)
        with patch("agentops.gui_controller.LogManager", return_value=logs):
            result = controller.read_log("/repo", "a.log")
        self.assertEqual(result["text"], "tail")
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
