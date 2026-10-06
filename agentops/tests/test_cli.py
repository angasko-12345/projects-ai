"""Tests for the AgentOps command line interface.

Every test drives the CLI through its dependency-injection seam
(``CliServices`` / ``_build_services``): each external service is
supplied by the test, so these tests never construct a real
repository, worktree, agent runner, or verification kernel, never
spawn Git, and never touch the system temporary directory. State
that must survive ``main``'s ``finally: state.close()`` is an
in-memory store behind a close-suppressing wrapper.
"""

import asyncio
import inspect
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.artifacts import Artifact, ArtifactKind, ArtifactMissingError
from agentops.cli import (
    CliServices,
    _build_services,
    _print_text,
    _resolve_state_base,
    build_parser,
    main,
)
from agentops.config import AgentConfig, AppConfig
from agentops.events import Event, EventSeverity, EventType
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)
from agentops.finalize import (
    WorktreeFinalization,
    finalize_for_outcome,
    record_worktree_provenance,
    retry_merge_for_worktree,
)
from agentops.git import GitError, GitWorktreeManager, Worktree, WorktreeRef
from agentops.logging import LogManager
from agentops.registry import AgentHealth, AgentRegistry, DetectedAgent
from agentops.runner import AgentRunner, RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification import Verifier
from agentops.verification_kernel import VerificationKernel
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
from agentops.workflow import WorkflowEngine, WorkflowResult


def _awaitable(result):
    """A coroutine that resolves to ``result`` (async factory)."""
    return asyncio.sleep(0, result=result)


# A pure path: the fake worktree manager reports it as the repository
# root, which makes every state/log/artifact location deterministic
# without touching the filesystem.
FAKE_ROOT = Path("/agentops-cli-test")
FAKE_WORKTREE = Worktree(
    repository=FAKE_ROOT,
    path=FAKE_ROOT / ".agentops" / "worktrees" / "agentops-test",
    branch="agentops/test",
    base_branch="main",
    base_commit="abc123def4567890",
)


class _KeptAliveStore:
    """StateStore whose ``close`` is a no-op.

    ``main`` closes the store it opens in ``finally``; an in-memory
    store loses all data when closed, so CLI tests keep the same
    seeded instance alive across a ``main`` call to assert on it.
    """

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def __getattr__(self, name):
        return getattr(self._store, name)

    def close(self) -> None:
        pass


class _NarrowStdout:
    """stdout whose text layer rejects non-cp1252 characters.

    Models a legacy Windows console: the text layer raises
    ``UnicodeEncodeError`` and the CLI must fall back to the binary
    buffer with replacement characters.
    """

    encoding = "cp1252"

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> int:
        if any(ord(char) > 127 for char in text):
            raise UnicodeEncodeError(
                "cp1252", text, 0, 1, "character not encodable")
        return len(text)

    def flush(self) -> None:
        pass


class FakeWorktreeManager:
    """GitWorktreeManager stand-in that touches no repository.

    ``repository_root`` returns a fixed path so state/log/artifact
    locations are deterministic; ``create`` returns a fixed worktree;
    every mutating call is recorded so tests can assert the CLI's
    worktree lifecycle without running Git.
    """

    def __init__(self, root: Path = FAKE_ROOT,
                 worktree: Worktree = FAKE_WORKTREE) -> None:
        self.root = root
        self.worktree = worktree
        self.roots: list[Path] = []
        self.created: list[tuple[Path, str]] = []
        self.removed: list[Worktree] = []
        self.merged: list[Worktree] = []
        self.committed: list[tuple[Worktree, str]] = []

    def repository_root(self, directory):
        self.roots.append(Path(directory))
        return self.root

    def create(self, directory, task_name):
        self.created.append((Path(directory), task_name))
        return self.worktree

    def remove(self, worktree):
        self.removed.append(worktree)

    def merge(self, worktree):
        self.merged.append(worktree)

    def commit_changes(self, worktree, message):
        self.committed.append((worktree, message))
        return True


class FakeRunner:
    """AgentRunner stand-in returning one canned RunResult."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    async def run_agent(self, agent, prompt, directory, task_id=None, **kwargs):
        self.calls.append((agent, prompt, directory, task_id))
        return self.result


class CliTestCase(unittest.TestCase):
    """Shared harness: an injected service bundle and output capture."""

    def setUp(self):
        self.manager = FakeWorktreeManager()
        self.services = self._services()

    def _services(self, **overrides) -> CliServices:
        manager = overrides.pop("worktree_manager", None) or self.manager
        fields: dict[str, object] = {
            "config": AppConfig({}, {}, (), max_attempts=3, concurrency=1),
            "state_store": lambda path: _in_memory_store(),
            "log_manager": lambda path: MagicMock(name="logs"),
            "artifact_store": lambda path: MagicMock(name="artifacts"),
            "agent_registry": lambda config: MagicMock(name="registry"),
            "agent_runner": lambda *args, **kwargs: MagicMock(name="runner"),
            "verifier": lambda *args, **kwargs: MagicMock(name="verifier"),
            "verification_kernel": lambda *args, **kwargs: MagicMock(name="kernel"),
            "workflow_engine": lambda *args, **kwargs: MagicMock(name="engine"),
            "worktree_manager": lambda: manager,
            "finalize": lambda *args, **kwargs: WorktreeFinalization(
                changed=True, merged=True, conflict_error=None),
            "record_provenance": lambda *args, **kwargs: True,
            "retry_merge": lambda *args, **kwargs: {"merged": True},
        }
        fields.update(overrides)
        return CliServices(**fields)

    def _invoke(self, argv, services=None):
        """Run main() capturing stdout and stderr."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv, services=services or self.services)
        return code, stdout.getvalue(), stderr.getvalue()


def _in_memory_store() -> _KeptAliveStore:
    return _KeptAliveStore(StateStore(":memory:"))


def _seeded_store() -> _KeptAliveStore:
    """In-memory store with one workflow holding one passed task."""
    store = StateStore(":memory:")
    workflow_id = store.create_workflow("ship the feature")
    task = store.add_task(Task("plan the change", "architecture", workflow_id))
    task.status = TaskStatus.PASSED
    store.update_task(task)
    return _KeptAliveStore(store)


def _finished_agent_run(store: StateStore, workflow_id: str, task_id: str,
                        status: AgentRunStatus = AgentRunStatus.COMPLETED,
                        exit_code: int = 0):
    run = store.create_agent_run(AgentRunContext(
        agent="demo", workflow_id=workflow_id, task_id=task_id))
    store.start_agent_run(run.id)
    store.mark_agent_run_running(run.id)
    store.finish_agent_run(run.id, AgentRunOutcome(
        status=status, exit_code=exit_code, duration_seconds=1.5))
    return run


def _finished_verification(store: StateStore, workflow_id: str, task_id: str,
                           overall: VerificationReportStatus = VerificationReportStatus.PASSED,
                           skipped_checks: int = 0):
    profile = VerificationProfile(
        name="unit", mode=VerificationProfileMode.FAIL_FAST, concurrency=1,
        default_timeout_seconds=300,
        checks=(VerificationCheckSpec(
            "unit", VerificationCheckClass.TESTS, ("unit",)),),
    )
    run = store.create_verification_run(workflow_id, task_id, profile)
    store.start_verification_run(run.id)
    check = store.create_verification_check(
        run.id, workflow_id, task_id, "unit", "unit", "tests", ("unit",),
        None, 300, True, "sequential")
    state = (VerificationCheckStatus.SKIPPED if skipped_checks
             else VerificationCheckStatus.PASSED)
    store.finish_verification_check(check.id, state, exit_code=0)
    store.finish_verification_run(
        run.id, VerificationRunStatus.COMPLETED, overall,
        total_checks=1, passed_checks=0 if skipped_checks else 1,
        failed_checks=0, skipped_checks=skipped_checks,
        required_failures=0, duration_seconds=0.5)
    store.create_verification_report(VerificationReport(
        id="report-1", run_id=run.id, workflow_id=workflow_id, task_id=task_id,
        profile_name="unit", total_checks=1,
        passed_checks=0 if skipped_checks else 1, failed_checks=0,
        skipped_checks=skipped_checks, required_failures=0,
        duration_seconds=0.5, overall_status=overall))
    return run


class BuildServicesTests(unittest.TestCase):
    """The production wiring seam."""

    def test_build_services_returns_the_production_stack(self):
        args = build_parser().parse_args(["status"])
        services = _build_services(args)
        self.assertIsInstance(services.config, AppConfig)
        self.assertIs(services.state_store, StateStore)
        self.assertIs(services.log_manager, LogManager)
        self.assertIs(services.agent_registry, AgentRegistry)
        self.assertIs(services.agent_runner, AgentRunner)
        self.assertIs(services.verifier, Verifier)
        self.assertIs(services.verification_kernel, VerificationKernel)
        self.assertIs(services.workflow_engine, WorkflowEngine)
        self.assertIs(services.worktree_manager, GitWorktreeManager)
        self.assertIs(services.finalize, finalize_for_outcome)
        self.assertIs(services.record_provenance, record_worktree_provenance)
        self.assertIs(services.retry_merge, retry_merge_for_worktree)

    def test_main_uses_the_injected_bundle(self):
        state = MagicMock(name="state")
        state.latest_workflow.return_value = None
        state_store = MagicMock(name="state_store")
        state_store.return_value = state
        services = CliServices(
            config=AppConfig({}, {}, (), max_attempts=1, concurrency=1),
            state_store=state_store,
            worktree_manager=lambda: FakeWorktreeManager(),
            # The CLI calls log_manager(state_root / "logs") for every command,
            # which creates real directories on disk. On CI the resolved root is
            # `/agentops-cli-test` and `/` is not writable, so the real
            # LogManager raises PermissionError. Inject a no-op so the test
            # stays filesystem-independent (it asserts on state_store only).
            log_manager=lambda root: MagicMock(name="logs"),
        )
        code, stdout, _ = _run(["status"], services)
        self.assertEqual(code, 0)
        self.assertIn("No persisted workflows.", stdout)
        # The injected factory opened the store at the resolved path.
        state_store.assert_called_once_with(
            FAKE_ROOT / ".agentops" / "state.sqlite")

    def test_main_builds_the_production_bundle_when_none_is_given(self):
        state = MagicMock(name="state")
        state.latest_workflow.return_value = None
        built = CliServices(
            config=AppConfig({}, {}, (), max_attempts=1, concurrency=1),
            state_store=lambda path: state,
            worktree_manager=lambda: FakeWorktreeManager(),
            # See test_main_uses_the_injected_bundle: the real LogManager
            # mkdir's the resolved state root, which is unwritable on CI.
            log_manager=lambda root: MagicMock(name="logs"),
        )
        with patch("agentops.cli._build_services", return_value=built) as build:
            code, stdout, _ = _run(["status"], None)
        self.assertEqual(code, 0)
        self.assertIn("No persisted workflows.", stdout)
        build.assert_called_once()

    def test_main_passes_the_config_path_to_the_production_loader(self):
        # `agents` loads the config but opens no state, so the
        # production loader runs against a patched load_config.
        config_path = Path("agents/agents.yaml")
        with patch("agentops.cli.load_config",
                   return_value=AppConfig({}, {}, (), max_attempts=1,
                                          concurrency=1)) as load:
            code, stdout, _ = _run(["--config", str(config_path), "agents"],
                                     None)
        self.assertEqual(code, 0)
        load.assert_called_once_with(config_path)


class StatusCommandTests(CliTestCase):
    def test_status_with_no_workflows(self):
        code, stdout, _ = self._invoke(["status"])
        self.assertEqual(code, 0)
        self.assertIn("No persisted workflows.", stdout)

    def test_status_reports_the_latest_workflow(self):
        store = _seeded_store()
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["status"], services)
        self.assertEqual(code, 0)
        workflow = store.latest_workflow()
        self.assertIn(workflow.id, stdout)
        self.assertIn("pending", stdout)
        self.assertIn("ship the feature", stdout)
        self.assertIn("passed", stdout)
        self.assertIn("architecture", stdout)
        self.assertIn("plan the change", stdout)

    def test_status_forwards_list_arguments(self):
        state = MagicMock(name="state")
        state.latest_workflow.return_value = SimpleNamespace(
            id="wf-1", status=TaskStatus.PENDING, description="desc")
        state.list_tasks.return_value = []
        state.list_agent_runs.return_value = []
        state.list_verification_runs.return_value = []
        state.list_failures.return_value = []
        state.get_worktree_ref.return_value = None
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(["status"], services)
        self.assertEqual(code, 0)
        state.list_agent_runs.assert_called_once_with("wf-1", limit=50)
        state.list_verification_runs.assert_called_once_with("wf-1", limit=20)
        state.list_failures.assert_called_once_with("wf-1", limit=10)

    def test_status_reports_runs_verifications_failures_and_worktree(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        _finished_agent_run(store, workflow_id, task_id)
        _finished_verification(store, workflow_id, task_id)
        store.create_failure(Failure(
            id="failure-1", workflow_id=workflow_id, task_id=task_id,
            agent_run_id=None, source=FailureSource.AGENT,
            category=FailureCategory.AGENT_ERROR, severity=FailureSeverity.HIGH,
            retryable=True, repairable=True, evidence="boom",
            recommended_action=RepairAction.RETRY_SAME_AGENT,
            recovery_state=RecoveryState.RECOVERED_FAILED))
        store.record_worktree_ref(WorktreeRef(
            id="ref-1", workflow_id=workflow_id,
            path=str(FAKE_WORKTREE.path), branch=FAKE_WORKTREE.branch,
            base_branch=FAKE_WORKTREE.base_branch,
            base_commit=FAKE_WORKTREE.base_commit, created_at=""))
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["status"], services)
        self.assertEqual(code, 0)
        self.assertIn("run completed", stdout)
        self.assertIn("task=" + task_id, stdout)
        self.assertIn("verification passed", stdout)
        self.assertIn("failure AGENT_ERROR", stdout)
        self.assertIn("retryable=True", stdout)
        self.assertIn("worktree agentops/test", stdout)
        self.assertIn("base=main@abc123def456", stdout)


class RunsCommandTests(CliTestCase):
    def test_runs_with_no_runs(self):
        code, stdout, _ = self._invoke(["runs"])
        self.assertEqual(code, 0)
        self.assertIn("No persisted agent runs.", stdout)

    def test_runs_lists_persisted_runs(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        run = _finished_agent_run(store, workflow_id, task_id)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["runs"], services)
        self.assertEqual(code, 0)
        self.assertIn(run.id, stdout)
        self.assertIn("completed", stdout)
        self.assertIn("demo", stdout)
        self.assertIn("task=" + task_id, stdout)
        self.assertIn("attempt=1", stdout)
        self.assertIn("exit=0", stdout)
        self.assertIn("1.5s", stdout)

    def test_runs_forwards_filters_and_pagination(self):
        state = MagicMock(name="state")
        state.list_agent_runs.return_value = []
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(
            ["runs", "--workflow", "wf-1", "--task", "task-1",
             "--status", "completed", "--limit", "5", "--offset", "2"],
            services)
        self.assertEqual(code, 0)
        self.assertIn("No persisted agent runs.", stdout)
        state.list_agent_runs.assert_called_once_with(
            "wf-1", "task-1", AgentRunStatus.COMPLETED, 5, 2)

    def test_runs_rejects_an_unknown_status(self):
        code, stdout, stderr = self._invoke(["runs", "--status", "bogus"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout)
        self.assertIn("bogus", stdout)


class VerifyCommandTests(CliTestCase):
    def test_verify_with_no_runs(self):
        code, stdout, _ = self._invoke(["verify"])
        self.assertEqual(code, 0)
        self.assertIn("No persisted verification runs.", stdout)

    def test_verify_lists_persisted_runs(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        run = _finished_verification(store, workflow_id, task_id)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["verify"], services)
        self.assertEqual(code, 0)
        self.assertIn(run.id, stdout)
        self.assertIn("passed", stdout)
        self.assertIn("unit", stdout)
        self.assertIn("task=" + task_id, stdout)
        self.assertIn("passed=1/1", stdout)
        self.assertIn("required_failures=0", stdout)

    def test_verify_details_one_run(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        run = _finished_verification(store, workflow_id, task_id)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["verify", "--run", run.id], services)
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  passed  profile=unit", stdout)
        self.assertIn("passed=1/1  failed=0 skipped=0  required_failures=0", stdout)
        self.assertIn("  passed     unit", stdout)
        self.assertIn("tests", stdout)
        self.assertIn("exit=0", stdout)
        self.assertIn("required=True", stdout)
        self.assertIn("reason=-", stdout)

    def test_verify_reports_skipped_checks(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        run = _finished_verification(
            store, workflow_id, task_id,
            overall=VerificationReportStatus.UNVERIFIED, skipped_checks=1)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["verify", "--run", run.id], services)
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  unverified  profile=unit", stdout)
        self.assertIn("passed=0/1  failed=0 skipped=1", stdout)
        self.assertIn("  skipped    unit", stdout)

    def test_verify_unknown_run_is_an_error(self):
        code, stdout, _ = self._invoke(["verify", "--run", "missing"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout)
        self.assertIn("missing", stdout)

    def test_verify_run_without_a_report_is_an_error(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        profile = VerificationProfile(
            name="unit", checks=(VerificationCheckSpec(
                "unit", VerificationCheckClass.TESTS, ("unit",)),))
        run = store.create_verification_run(workflow_id, task_id, profile)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["verify", "--run", run.id], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout)

    def test_verify_forwards_filters_and_pagination(self):
        state = MagicMock(name="state")
        state.list_verification_runs.return_value = []
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(
            ["verify", "--workflow", "wf-1", "--task", "task-1",
             "--limit", "5", "--offset", "2"], services)
        self.assertEqual(code, 0)
        self.assertIn("No persisted verification runs.", stdout)
        state.list_verification_runs.assert_called_once_with(
            "wf-1", "task-1", limit=5, offset=2)


class EventsCommandTests(CliTestCase):
    def test_events_with_no_events(self):
        code, stdout, _ = self._invoke(["events"])
        self.assertEqual(code, 0)
        self.assertIn("No timeline events.", stdout)

    def test_events_lists_the_timeline(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        event = store.record_typed_event(Event(
            workflow_id=workflow_id, task_id=task_id,
            type=EventType.TASK_COMPLETED, severity=EventSeverity.INFO,
            message="task completed"))
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["events"], services)
        self.assertEqual(code, 0)
        self.assertIn("task.completed", stdout)
        self.assertIn("info", stdout)
        self.assertIn("task completed", stdout)
        self.assertIn("workflow=" + workflow_id, stdout)
        self.assertIn("task=" + task_id, stdout)

    def test_events_forwards_filters_and_pagination(self):
        state = MagicMock(name="state")
        state.query_events.return_value = []
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(
            ["events", "--workflow", "wf-1", "--task", "task-1",
             "--run", "run-1", "--type", "task.completed",
             "--limit", "5", "--offset", "2"], services)
        self.assertEqual(code, 0)
        self.assertIn("No timeline events.", stdout)
        state.query_events.assert_called_once_with(
            "wf-1", "task-1", "run-1", "task.completed", 5, 2)


class ArtifactsCommandTests(CliTestCase):
    def test_artifacts_with_no_artifacts(self):
        code, stdout, _ = self._invoke(["artifacts"])
        self.assertEqual(code, 0)
        self.assertIn("No stored artifacts.", stdout)

    def test_artifacts_lists_stored_artifacts(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        artifact = store.create_artifact_record(Artifact(
            workflow_id=workflow_id, task_id=task_id, kind=ArtifactKind.PLANS,
            name="plan.md", rel_path="plans/wf/plan.md",
            sha256="ab" * 32, size_bytes=120))
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["artifacts"], services)
        self.assertEqual(code, 0)
        self.assertIn(artifact.id, stdout)
        self.assertIn("plans", stdout)
        self.assertIn("plan.md", stdout)
        self.assertIn("120B", stdout)
        self.assertIn("abababab", stdout)
        self.assertIn("workflow=" + workflow_id, stdout)

    def test_artifacts_forwards_filters_and_pagination(self):
        state = MagicMock(name="state")
        state.list_artifacts.return_value = []
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(
            ["artifacts", "--workflow", "wf-1", "--task", "task-1",
             "--kind", "plans", "--limit", "5", "--offset", "2"], services)
        self.assertEqual(code, 0)
        self.assertIn("No stored artifacts.", stdout)
        state.list_artifacts.assert_called_once_with(
            "wf-1", "task-1", "plans", 5, 2)

    def test_artifacts_show_prints_the_stored_content(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        artifact = store.create_artifact_record(Artifact(
            workflow_id=workflow_id, task_id=task_id, kind=ArtifactKind.PLANS,
            name="plan.md", rel_path="plans/wf/plan.md",
            sha256="ab" * 32, size_bytes=120))
        artifact_store = MagicMock(name="artifact_store")
        artifact_store.read_text.return_value = "the plan"
        services = self._services(
            state_store=lambda path: store,
            artifact_store=lambda path: artifact_store)
        code, stdout, _ = self._invoke(["artifacts", "--show", artifact.id],
                                       services)
        self.assertEqual(code, 0)
        self.assertIn("the plan", stdout)
        artifact_store.read_text.assert_called_once_with(artifact)

    def test_artifacts_show_unknown_artifact_is_an_error(self):
        code, stdout, _ = self._invoke(["artifacts", "--show", "missing"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout)
        self.assertIn("missing", stdout)

    def test_artifacts_show_unreadable_artifact_is_an_error(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        artifact = store.create_artifact_record(Artifact(
            workflow_id=workflow_id, task_id=task_id, kind=ArtifactKind.PLANS,
            name="plan.md", rel_path="plans/wf/plan.md",
            sha256="ab" * 32, size_bytes=120))
        artifact_store = MagicMock(name="artifact_store")
        artifact_store.read_text.side_effect = ArtifactMissingError(
            "artifact file missing")
        services = self._services(
            state_store=lambda path: store,
            artifact_store=lambda path: artifact_store)
        code, stdout, _ = self._invoke(["artifacts", "--show", artifact.id],
                                       services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout)

    def test_artifacts_prune_keeps_the_newest_and_deletes_the_rest(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        kept = store.create_artifact_record(Artifact(
            workflow_id=workflow_id, task_id=task_id, kind=ArtifactKind.PLANS,
            name="newest", rel_path="plans/wf/newest",
            sha256="cd" * 32, size_bytes=10))
        pruned = store.create_artifact_record(Artifact(
            workflow_id=workflow_id, task_id=task_id, kind=ArtifactKind.PLANS,
            name="oldest", rel_path="plans/wf/oldest",
            sha256="ef" * 32, size_bytes=10))
        artifact_store = MagicMock(name="artifact_store")
        artifact_store.scan_files.return_value = [
            "plans/wf/newest", "plans/wf/oldest"]
        artifact_store.prune.return_value = [pruned.id]
        artifact_store.find_orphans.return_value = []
        state = MagicMock(name="state")
        state.list_artifacts.return_value = [pruned, kept]
        state.delete_artifact_record.return_value = True
        services = self._services(
            state_store=lambda path: state,
            artifact_store=lambda path: artifact_store)
        code, stdout, _ = self._invoke(["artifacts", "--prune-keep", "1"],
                                       services)
        self.assertEqual(code, 0)
        self.assertIn("Pruned 1 artifact(s).", stdout)
        artifact_store.prune.assert_called_once_with([pruned, kept],
                                                     keep_last_n=1)
        state.delete_artifact_record.assert_called_once_with(pruned.id)


class FailuresCommandTests(CliTestCase):
    def test_failures_with_no_failures(self):
        code, stdout, _ = self._invoke(["failures"])
        self.assertEqual(code, 0)
        self.assertIn("No persisted failures.", stdout)

    def test_failures_lists_persisted_failures(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task_id = store.list_tasks(workflow_id)[0].id
        failure = store.create_failure(Failure(
            id="failure-1", workflow_id=workflow_id, task_id=task_id,
            agent_run_id=None, source=FailureSource.AGENT,
            category=FailureCategory.TIMEOUT, severity=FailureSeverity.HIGH,
            retryable=True, repairable=False, evidence="timed out",
            recommended_action=RepairAction.RETRY_SAME_AGENT,
            recovery_state=RecoveryState.INTERRUPTED_AGENT_EXECUTION))
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["failures"], services)
        self.assertEqual(code, 0)
        self.assertIn("failure-1", stdout)
        self.assertIn("TIMEOUT", stdout)
        self.assertIn("HIGH", stdout)
        self.assertIn("retry_same_agent", stdout)
        self.assertIn("task=" + task_id, stdout)
        self.assertIn("retryable=True", stdout)
        self.assertIn("repairable=False", stdout)
        self.assertIn("interrupted_agent_execution", stdout)

    def test_failures_forwards_filters_and_pagination(self):
        state = MagicMock(name="state")
        state.list_failures.return_value = []
        services = self._services(state_store=lambda path: state)
        code, stdout, _ = self._invoke(
            ["failures", "--workflow", "wf-1", "--task", "task-1",
             "--category", "TIMEOUT", "--limit", "5", "--offset", "2"],
            services)
        self.assertEqual(code, 0)
        self.assertIn("No persisted failures.", stdout)
        state.list_failures.assert_called_once_with(
            "wf-1", "task-1", "TIMEOUT", 5, 2)


class RecoverCommandTests(CliTestCase):
    def test_recover_with_nothing_interrupted(self):
        store = _in_memory_store()
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["recover"], services)
        self.assertEqual(code, 0)
        self.assertIn("Recovered agent_runs=0 verification_runs=0 tasks=0.",
                      stdout)
        self.assertIn("Interrupted work is marked failed "
                      "(never success without evidence); worktrees preserved.",
                      stdout)

    def test_recover_marks_interrupted_work_failed(self):
        store = _seeded_store()
        workflow_id = store.latest_workflow().id
        task = store.list_tasks(workflow_id)[0]
        # A task stranded RUNNING by a crash, plus a RUNNING agent run
        # and a RUNNING verification run.
        task.status = TaskStatus.RUNNING
        store.update_task(task)
        _finished_agent_run(store, workflow_id, task.id,
                            status=AgentRunStatus.RUNNING, exit_code=None)
        profile = VerificationProfile(
            name="unit", checks=(VerificationCheckSpec(
                "unit", VerificationCheckClass.TESTS, ("unit",)),))
        verification = store.create_verification_run(
            workflow_id, task.id, profile)
        store.start_verification_run(verification.id)
        services = self._services(state_store=lambda path: store)
        code, stdout, _ = self._invoke(["recover"], services)
        self.assertEqual(code, 0)
        self.assertIn("Recovered agent_runs=1 verification_runs=1 tasks=1.",
                      stdout)
        self.assertIn("Interrupted work is marked failed "
                      "(never success without evidence); worktrees preserved.",
                      stdout)
        # Recovery must never mint success: everything stranded is now
        # terminal and failed/terminated.
        self.assertEqual(store.get_task(task.id).status, TaskStatus.FAILED)
        runs = store.list_agent_runs(workflow_id)
        self.assertTrue(runs)
        self.assertTrue(all(run.status is AgentRunStatus.TERMINATED
                            for run in runs))
        verifications = store.list_verification_runs(workflow_id)
        self.assertTrue(verifications)
        self.assertTrue(all(run.status is VerificationRunStatus.FAILED
                            for run in verifications))


class LogsCommandTests(CliTestCase):
    def test_logs_with_no_logs(self):
        logs = MagicMock(name="logs")
        logs.list_logs.return_value = []
        services = self._services(log_manager=lambda path: logs)
        code, stdout, _ = self._invoke(["logs"], services)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")

    def test_logs_lists_paths_newest_first(self):
        logs = MagicMock(name="logs")
        newest = Path("/agentops-cli-test/.agentops/logs/newest.log")
        oldest = Path("/agentops-cli-test/.agentops/logs/oldest.log")
        logs.list_logs.return_value = [newest, oldest]
        services = self._services(log_manager=lambda path: logs)
        code, stdout, _ = self._invoke(["logs"], services)
        self.assertEqual(code, 0)
        self.assertIn(str(newest), stdout)
        self.assertIn(str(oldest), stdout)
        # The full list is returned, newest first.
        logs.list_logs.assert_called_once_with(None)

    def test_logs_forwards_task_and_limits_the_tail(self):
        logs = MagicMock(name="logs")
        newest = Path("/agentops-cli-test/.agentops/logs/newest.log")
        oldest = Path("/agentops-cli-test/.agentops/logs/oldest.log")
        logs.list_logs.return_value = [newest, oldest]
        services = self._services(log_manager=lambda path: logs)
        code, stdout, _ = self._invoke(
            ["logs", "--task", "task-1", "--tail", "1"], services)
        self.assertEqual(code, 0)
        # Only the newest of the two survives the --tail 1 slice.
        self.assertIn(str(newest), stdout)
        self.assertNotIn(str(oldest), stdout)
        logs.list_logs.assert_called_once_with("task-1")


class AgentsCommandTests(CliTestCase):
    def _registry_with(self, *detected):
        registry = MagicMock(name="registry")
        registry.detect.return_value = {
            agent.config.name: agent for agent in detected}
        return registry

    def test_agents_reports_health_and_reason(self):
        config = AgentConfig("demo", "demo", ("{prompt}",))
        registry = self._registry_with(
            DetectedAgent(config, True, "demo",
                          health=AgentHealth.UNHEALTHY,
                          health_detail="health probe timed out"))
        services = self._services(agent_registry=lambda config: registry)
        code, stdout, _ = self._invoke(["agents"], services)
        self.assertEqual(code, 0)
        self.assertIn("demo", stdout)
        self.assertIn("UNHEALTHY", stdout)
        self.assertIn("(health probe timed out)", stdout)

    def test_agents_reports_disabled_and_missing_states(self):
        enabled = AgentConfig("blocked", "blocked", ("{prompt}",),
                              enabled=False)
        registry = self._registry_with(
            DetectedAgent(enabled, False, None, health=AgentHealth.BLOCKED),
            DetectedAgent(AgentConfig("gone", "gone", ("{prompt}",)),
                          False, None, health=AgentHealth.DISCOVERED))
        services = self._services(agent_registry=lambda config: registry)
        code, stdout, _ = self._invoke(["agents"], services)
        self.assertEqual(code, 0)
        self.assertIn("DISABLED", stdout)
        self.assertIn("MISSING", stdout)


class RunCommandTests(CliTestCase):
    def _run_services(self, runner, registry=None, store=None):
        registry = registry or MagicMock(name="registry")
        agent = DetectedAgent(AgentConfig("demo", "demo", ("{prompt}",)),
                               True, "demo")
        registry.get.return_value = agent
        return self._services(
            state_store=lambda path: store or _in_memory_store(),
            agent_registry=lambda config: registry,
            agent_runner=lambda *args, **kwargs: runner)

    def test_run_reports_a_successful_run(self):
        result = RunResult(
            agent="demo", command=("demo",), exit_code=0,
            stdout="did the work", stderr="", duration_seconds=0.5,
            timed_out=False, log_path=Path("/agentops-cli-test/demo.log"),
            run_id="run-1")
        runner = FakeRunner(result)
        services = self._run_services(runner)
        code, stdout, _ = self._invoke(["run", "demo", "do the work"], services)
        self.assertEqual(code, 0)
        self.assertIn("[demo] PASSED (0.5s)", stdout)
        self.assertIn("run: run-1", stdout)
        self.assertIn(f"log: {result.log_path}", stdout)
        self.assertIn("did the work", stdout)
        # The runner was dispatched with the agent, the prompt, and the
        # working directory.
        self.assertEqual(len(runner.calls), 1)
        agent, prompt, directory, task_id = runner.calls[0]
        self.assertEqual(agent.config.name, "demo")
        self.assertEqual(prompt, "do the work")
        self.assertEqual(directory, Path.cwd())

    def test_run_reports_a_failed_run(self):
        result = RunResult(
            agent="demo", command=("demo",), exit_code=1, stdout="",
            stderr="boom", duration_seconds=0.5, timed_out=False,
            log_path=Path("/agentops-cli-test/demo.log"), run_id="run-2")
        runner = FakeRunner(result)
        services = self._run_services(runner)
        code, stdout, _ = self._invoke(["run", "demo", "do the work"], services)
        self.assertEqual(code, 1)
        self.assertIn("[demo] FAILED (0.5s)", stdout)
        self.assertIn("boom", stdout)

    def test_run_reports_a_timed_out_run_as_a_failure(self):
        result = RunResult(
            agent="demo", command=("demo",), exit_code=None, stdout="",
            stderr="", duration_seconds=30.0, timed_out=True,
            log_path=Path("/agentops-cli-test/demo.log"), run_id="run-3")
        runner = FakeRunner(result)
        services = self._run_services(runner)
        code, stdout, _ = self._invoke(["run", "demo", "do the work"], services)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", stdout)

    def test_run_unknown_agent_is_an_error(self):
        registry = MagicMock(name="registry")
        registry.get.side_effect = KeyError("Unknown agent: nope")
        result = RunResult(
            agent="demo", command=("demo",), exit_code=0, stdout="",
            stderr="", duration_seconds=0.0, timed_out=False,
            log_path=Path("/agentops-cli-test/demo.log"), run_id="run-x")
        services = self._run_services(FakeRunner(result), registry=registry)
        code, stdout, _ = self._invoke(["run", "nope", "do it"], services)
        self.assertEqual(code, 1)
        self.assertIn("ERROR:", stdout)
        self.assertIn("Unknown agent: nope", stdout)

    def test_run_runner_failure_is_an_error(self):
        class _ExplodingRunner:
            async def run_agent(self, *args, **kwargs):
                raise RuntimeError("runner exploded")
        services = self._run_services(_ExplodingRunner())
        code, stdout, _ = self._invoke(["run", "demo", "do it"], services)
        self.assertEqual(code, 1)
        self.assertIn("ERROR:", stdout)
        self.assertIn("runner exploded", stdout)


class TaskWorkflowCommandTests(CliTestCase):
    """The task/workflow dispatch, readiness, and merge gate."""

    def _workflow_services(self, engine, manager=None, finalize=None,
                           state=None, record_provenance=None,
                           worktree_manager=None):
        if state is None:
            # The CLI iterates these two results in the task/workflow
            # tail, and a bare MagicMock is not iterable, so a freshly
            # built state needs both configured. A caller-supplied
            # state keeps its own configuration.
            state = MagicMock(name="state")
            state.list_tasks.return_value = []
            state.list_verification_runs.return_value = []
        # Share the test case's manager so worktree lifecycle
        # assertions observe what the CLI actually did.
        manager = manager or self.manager
        return self._services(
            worktree_manager=worktree_manager or manager,
            state_store=lambda path: state,
            workflow_engine=lambda *args, **kwargs: engine,
            finalize=finalize or (lambda *args, **kwargs: WorktreeFinalization(
                changed=True, merged=True, conflict_error=None)),
            record_provenance=record_provenance
            or (lambda *args, **kwargs: True),
        )

    def _ready_engine(self, workflow_id="wf-1", summary="READY"):
        engine = MagicMock(name="engine")
        engine.run_high_level = MagicMock(
            return_value=_awaitable(WorkflowResult(
                workflow_id, True, summary)))
        return engine

    def test_task_merges_and_removes_the_worktree_when_ready(self):
        engine = self._ready_engine()
        finalize = MagicMock(return_value=WorktreeFinalization(
            changed=True, merged=True, conflict_error=None))
        record_provenance = MagicMock(return_value=True)
        services = self._workflow_services(
            engine, finalize=finalize, record_provenance=record_provenance)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 0)
        self.assertIn("RESULT: READY", stdout)
        self.assertIn("workflow: wf-1", stdout)
        self.assertIn("merged worktree changes", stdout)
        engine.run_high_level.assert_called_once_with(
            "ship it", FAKE_WORKTREE.path)
        finalize.assert_called_once()
        args = finalize.call_args.args
        self.assertEqual(args[3], "ship it")
        self.assertEqual(args[4], "wf-1")
        self.assertEqual(args[5], 3)  # config.max_attempts
        self.assertTrue(finalize.call_args.kwargs["ready"])
        # The worktree was removed and provenance was recorded.
        self.assertEqual(self.manager.removed, [FAKE_WORKTREE])
        record_provenance.assert_called_once()
        self.assertIs(record_provenance.call_args.args[1], FAKE_WORKTREE)
        self.assertEqual(record_provenance.call_args.args[2], "wf-1")

    def test_task_reports_no_changes_when_nothing_changed(self):
        engine = self._ready_engine()
        finalize = MagicMock(return_value=WorktreeFinalization(
            changed=False, merged=False, conflict_error=None))
        services = self._workflow_services(engine, finalize=finalize)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 0)
        self.assertIn("no worktree changes", stdout)
        # A READY result still removes the worktree.
        self.assertEqual(self.manager.removed, [FAKE_WORKTREE])

    def test_task_does_not_merge_when_not_ready_and_implementation_failed(self):
        engine = MagicMock(name="engine")
        engine.run_high_level = MagicMock(
            return_value=_awaitable(WorkflowResult(
                "wf-1", False, "Review did not pass.")))
        state = MagicMock(name="state")
        state.list_tasks.return_value = []  # no PASSED implementation task
        state.list_verification_runs.return_value = []
        finalize = MagicMock(return_value=WorktreeFinalization(
            changed=False, merged=False, conflict_error=None))
        services = self._workflow_services(
            engine, finalize=finalize, state=state)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        # Not READY with a failed implementation: no commit, no
        # merge, worktree preserved. The command itself ran to
        # completion, so it is not an error exit.
        self.assertEqual(code, 0)
        self.assertIn("RESULT: Review did not pass.", stdout)
        self.assertIn("no worktree changes", stdout)
        finalize.assert_not_called()
        self.assertEqual(self.manager.removed, [])
        self.assertIn("Worktree preserved at", stdout)

    def test_task_commits_unverified_work_when_implementation_passed(self):
        """Delegate work is committed even when verification did not pass."""
        engine = MagicMock(name="engine")
        engine.run_high_level = MagicMock(
            return_value=_awaitable(WorkflowResult(
                "wf-1", False, "Verification was unverified")))
        state = MagicMock(name="state")
        state.list_tasks.return_value = [
            Task("impl", "implementation", "wf-1", status=TaskStatus.PASSED)]
        state.list_verification_runs.return_value = []
        finalize = MagicMock(return_value=WorktreeFinalization(
            changed=True, merged=False, conflict_error=None))
        services = self._workflow_services(
            engine, finalize=finalize, state=state)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        # Not READY, but the implementation passed: the delegate
        # work is committed to the agentops branch and preserved
        # for `agentops retry-merge`, deliberately not merged.
        # The command itself ran to completion, so it is not an
        # error exit.
        self.assertEqual(code, 0)
        self.assertIn("work committed but NOT merged (workflow not READY)",
                      stdout)
        finalize.assert_called_once()
        self.assertFalse(finalize.call_args.kwargs["ready"])
        self.assertEqual(self.manager.merged, [])
        self.assertEqual(self.manager.removed, [])

    def test_task_preserves_the_worktree_on_a_merge_conflict(self):
        engine = self._ready_engine()
        finalize = MagicMock(return_value=WorktreeFinalization(
            changed=True, merged=False, conflict_error="merge conflict"))
        services = self._workflow_services(engine, finalize=finalize)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT: merge conflict", stdout)
        self.assertIn("A persisted conflict-resolution task was created; "
                      "the worktree is preserved.", stdout)
        self.assertEqual(self.manager.removed, [])

    def test_task_reports_engine_failures(self):
        engine = MagicMock(name="engine")
        engine.run_high_level = MagicMock(
            side_effect=RuntimeError("engine exploded"))
        services = self._workflow_services(engine)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: engine exploded", stdout)
        self.assertEqual(self.manager.removed, [])

    def test_task_fails_fast_outside_a_git_repository(self):
        manager = FakeWorktreeManager()
        manager.repository_root = MagicMock(
            side_effect=GitError("Not a Git repository"))
        engine = MagicMock(name="engine")
        services = self._workflow_services(
            engine, worktree_manager=manager)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 1)
        self.assertIn("ERROR:", stdout)
        self.assertIn("Not a Git repository", stdout)
        # No worktree was created and no state was opened.
        self.assertEqual(manager.created, [])

    def test_task_reports_a_skipped_verification_summary(self):
        engine = self._ready_engine()
        state = MagicMock(name="state")
        state.list_tasks.return_value = []
        state.list_verification_runs.return_value = [SimpleNamespace(id="vr-1")]
        state.get_verification_report_by_run.return_value = SimpleNamespace(
            skipped_checks=2, total_checks=3,
            overall_status=VerificationReportStatus.UNVERIFIED)
        services = self._workflow_services(engine, state=state)
        code, stdout, _ = self._invoke(["task", "ship it"], services)
        self.assertEqual(code, 0)
        self.assertIn("verification unverified: 2 of 3 check(s) SKIPPED", stdout)
        self.assertIn("this is UNVERIFIED rather than verified", stdout)
        self.assertIn("agentops verify --run vr-1", stdout)

    def test_workflow_without_tasks_runs_the_high_level_flow(self):
        engine = self._ready_engine()
        services = self._workflow_services(engine)
        definition = {"description": "high level"}
        with patch("agentops.cli._load_data", return_value=definition):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        self.assertEqual(code, 0)
        self.assertIn("RESULT: READY", stdout)
        engine.run_high_level.assert_called_once_with(
            "high level", FAKE_WORKTREE.path)

    def test_workflow_with_tasks_runs_the_dependency_graph(self):
        engine = MagicMock(name="engine")
        engine.create_workflow.return_value = (
            "wf-1", [Task("a", "implementation", "wf-1"),
                     Task("b", "verification", "wf-1")])
        engine.execute = MagicMock(return_value=_awaitable(None))
        engine.workflow_readiness.return_value = SimpleNamespace(
            ready=True, summary=lambda: "READY")
        services = self._workflow_services(engine)
        definition = {
            "description": "custom flow",
            "tasks": [
                {"id": "a", "description": "first", "role": "implementation"},
                {"id": "b", "description": "second", "role": "verification",
                 "dependencies": ["a"]},
            ],
        }
        with patch("agentops.cli._load_data", return_value=definition):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        self.assertEqual(code, 0)
        self.assertIn("RESULT: READY", stdout)
        engine.create_workflow.assert_called_once_with(
            "custom flow", definition["tasks"])
        engine.execute.assert_called_once_with("wf-1", FAKE_WORKTREE.path)
        engine.workflow_readiness.assert_called_once_with("wf-1")

    def test_workflow_dependency_graph_not_ready_is_an_error(self):
        engine = MagicMock(name="engine")
        engine.create_workflow.return_value = ("wf-1", [])
        engine.execute = MagicMock(return_value=_awaitable(None))
        engine.workflow_readiness.return_value = SimpleNamespace(
            ready=False, summary=lambda: "NOT READY")
        services = self._workflow_services(engine)
        definition = {"description": "custom flow", "tasks": [
            {"id": "a", "description": "first", "role": "implementation"}]}
        with patch("agentops.cli._load_data", return_value=definition):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        # A not-READY DAG is a preserved outcome, not an error:
        # the work is kept for `agentops retry-merge`.
        self.assertEqual(code, 0)
        self.assertIn("RESULT: NOT READY", stdout)
        self.assertIn("no worktree changes", stdout)

    def test_workflow_file_requiring_a_description(self):
        services = self._workflow_services(MagicMock(name="engine"))
        with patch("agentops.cli._load_data",
                   return_value={"tasks": []}):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: workflow file requires a string 'description'.",
                      stdout)

    def test_workflow_file_with_a_non_list_tasks_value(self):
        services = self._workflow_services(MagicMock(name="engine"))
        with patch("agentops.cli._load_data",
                   return_value={"description": "d", "tasks": "nope"}):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: workflow 'tasks' must be a list of mappings.",
                      stdout)

    def test_workflow_file_with_a_non_mapping_task(self):
        services = self._workflow_services(MagicMock(name="engine"))
        with patch("agentops.cli._load_data",
                   return_value={"description": "d", "tasks": ["nope"]}):
            code, stdout, _ = self._invoke(
                ["workflow", "flow.yaml"], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: workflow 'tasks' must be a list of mappings.",
                      stdout)

    def test_workflow_missing_file_is_an_error(self):
        services = self._workflow_services(MagicMock(name="engine"))
        with patch("agentops.cli._load_data",
                   side_effect=FileNotFoundError("no such file")):
            code, stdout, _ = self._invoke(
                ["workflow", "missing.yaml"], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: invalid workflow file", stdout)

    def test_workflow_file_with_an_invalid_format_is_an_error(self):
        services = self._workflow_services(MagicMock(name="engine"))
        with patch("agentops.cli._load_data",
                   side_effect=ValueError("Configuration root must be a mapping.")):
            code, stdout, _ = self._invoke(
                ["workflow", "bad.yaml"], services)
        self.assertEqual(code, 2)
        self.assertIn("ERROR: invalid workflow file", stdout)


class RetryMergeCommandTests(CliTestCase):
    def _retry_merge_services(self, outcome=None, manager=None):
        manager = manager or FakeWorktreeManager()
        retry_merge = MagicMock(return_value=outcome or {
            "merged": True, "path": str(FAKE_WORKTREE.path),
            "branch": FAKE_WORKTREE.branch,
            "base_branch": FAKE_WORKTREE.base_branch,
            "used_stored_provenance": True})
        return self._services(
            worktree_manager=manager,
            state_store=lambda path: _in_memory_store(),
            retry_merge=retry_merge), retry_merge

    def test_retry_merge_merges_a_preserved_worktree(self):
        services, retry_merge = self._retry_merge_services()
        code, stdout, _ = self._invoke(
            ["retry-merge", str(FAKE_WORKTREE.path)], services)
        self.assertEqual(code, 0)
        self.assertIn("merged agentops/test into main", stdout)
        retry_merge.assert_called_once()
        args = retry_merge.call_args.args
        self.assertEqual(args[3], FAKE_WORKTREE.path)

    def test_retry_merge_warns_when_provenance_was_not_stored(self):
        services, retry_merge = self._retry_merge_services(outcome={
            "merged": True, "path": str(FAKE_WORKTREE.path),
            "branch": FAKE_WORKTREE.branch,
            "base_branch": FAKE_WORKTREE.base_branch,
            "used_stored_provenance": False})
        code, stdout, _ = self._invoke(
            ["retry-merge", str(FAKE_WORKTREE.path)], services)
        self.assertEqual(code, 0)
        self.assertIn("WARNING: no stored provenance for this worktree; "
                      "merged against the current HEAD of the base branch.",
                      stdout)

    def test_retry_merge_reports_a_git_error(self):
        services, retry_merge = self._retry_merge_services()
        retry_merge.side_effect = GitError("merge failed")
        code, stdout, _ = self._invoke(
            ["retry-merge", str(FAKE_WORKTREE.path)], services)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: merge failed", stdout)


class ParserAndArgumentTests(CliTestCase):
    def test_no_command_prints_usage_and_exits_2(self):
        with self.assertRaises(SystemExit) as caught:
            self._invoke([])
        self.assertEqual(caught.exception.code, 2)

    def test_unknown_command_prints_usage_and_exits_2(self):
        with self.assertRaises(SystemExit) as caught:
            self._invoke(["bogus"])
        self.assertEqual(caught.exception.code, 2)

    def test_run_requires_a_prompt(self):
        with self.assertRaises(SystemExit) as caught:
            self._invoke(["run", "demo"])
        self.assertEqual(caught.exception.code, 2)

    def test_version_prints_the_version_and_exits_0(self):
        with self.assertRaises(SystemExit) as caught:
            self._invoke(["--version"])
        self.assertEqual(caught.exception.code, 0)

    def test_missing_config_file_is_an_error(self):
        # `agents` loads the config but opens no state, so the
        # production config loader runs against a missing file.
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", "does-not-exist.yaml", "agents"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", stdout.getvalue())

    def test_invalid_config_is_an_error(self):
        with patch("agentops.cli.load_config",
                   side_effect=ValueError("agents must be a mapping.")):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["agents"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR: agents must be a mapping.", stdout.getvalue())


class PrintTextTests(unittest.TestCase):
    """The narrow-console encoding fallback."""

    def test_print_text_writes_plain_text(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            _print_text("plain result")
        self.assertEqual(stdout.getvalue(), "plain result\n")

    def test_print_text_falls_back_to_the_binary_buffer(self):
        narrow = _NarrowStdout()
        with patch("sys.stdout", narrow):
            _print_text("result → done")
        self.assertIn(b"result ? done\n", narrow.buffer.getvalue())

    def test_run_command_falls_back_to_the_binary_buffer(self):
        result = RunResult(
            agent="demo", command=("demo",), exit_code=0,
            stdout="result → done", stderr="", duration_seconds=0.5,
            timed_out=False, log_path=Path("/agentops-cli-test/demo.log"),
            run_id="run-1")
        registry = MagicMock(name="registry")
        registry.get.return_value = DetectedAgent(
            AgentConfig("demo", "demo", ("{prompt}",)), True, "demo")
        narrow = _NarrowStdout()
        services = CliServices(
            config=AppConfig({}, {}, (), max_attempts=1, concurrency=1),
            state_store=lambda path: _in_memory_store(),
            log_manager=lambda path: MagicMock(name="logs"),
            agent_registry=lambda config: registry,
            agent_runner=lambda *args, **kwargs: FakeRunner(result),
            worktree_manager=lambda: FakeWorktreeManager(),
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", narrow), redirect_stderr(stderr):
            code = main(["run", "demo", "do the work"], services=services)
        self.assertEqual(code, 0)
        # The non-cp1252 arrow is replaced on the binary fallback.
        self.assertIn(b"result ? done\n", narrow.buffer.getvalue())


class ResolveStateBaseTests(unittest.TestCase):
    """The state-root resolution seam keeps its one-argument form."""

    def test_non_git_directory_falls_back(self):
        manager = MagicMock(name="manager")
        manager.repository_root.side_effect = GitError("not a repo")
        self.assertEqual(
            _resolve_state_base(Path("/somewhere"), lambda: manager),
            Path("/somewhere"))

    def test_git_repository_resolves_the_root(self):
        manager = MagicMock(name="manager")
        manager.repository_root.return_value = Path("/repo")
        self.assertEqual(
            _resolve_state_base(Path("/repo/sub"), lambda: manager),
            Path("/repo"))

    def test_default_manager_is_the_real_worktree_manager(self):
        # The production default is bound at definition time, so the
        # real manager is what a one-argument call uses.
        default = inspect.signature(_resolve_state_base).parameters[
            "worktree_manager"].default
        self.assertIs(default, GitWorktreeManager)


class CliTests(unittest.TestCase):
    """Pre-existing CLI behaviour that must keep holding."""

    def test_agents_command_reports_known_profiles(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["agents"])
        self.assertEqual(exit_code, 0)
        self.assertIn("fcc-claude", output.getvalue())
        self.assertIn("codex", output.getvalue())

    def test_workflow_requires_description(self):
        with self.assertRaises(SystemExit):
            main([])


def _run(argv, services):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv, services=services)
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
