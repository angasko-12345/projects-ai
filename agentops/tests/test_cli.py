"""CLI behavior tests.

Every command is exercised through ``main`` with an injected
:class:`~agentops.cli.CliServices` bundle, so the assertions cover exit codes,
printed output, and the exact arguments the CLI passes to each collaborator
without ever constructing a real SQLite file, Git worktree, or subprocess.
"""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentops import cli as cli_module
from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus
from agentops.artifacts import ArtifactStore
from agentops.cli import CliServices, _build_services, _resolve_state_base, build_parser, main
from agentops.config import AppConfig
from agentops.events import Event, EventSeverity, EventType
from agentops.execution_model import WorkflowReadiness
from agentops.failure import (
    Failure,
    FailureCategory,
    FailureSeverity,
    FailureSource,
    RecoveryState,
    RepairAction,
)
from agentops.git import GitError, Worktree, WorktreeRef
from agentops.registry import AgentHealth
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus
from agentops.verification_model import (
    VerificationCheckStatus,
    VerificationProfile,
    VerificationReport,
    VerificationReportStatus,
    VerificationRunStatus,
)
from agentops.workflow import WorkflowResult


def _config(**overrides) -> AppConfig:
    settings: dict = {"agents": {}, "role_preferences": {}, "verification_commands": ()}
    settings.update(overrides)
    return AppConfig(**settings)


class SpyState:
    """Real in-memory ``StateStore`` that also records how it was called."""

    def __init__(self):
        self.store = StateStore(":memory:")
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        attribute = getattr(self.store, name)
        if not callable(attribute):
            return attribute

        def recorder(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return attribute(*args, **kwargs)

        return recorder

    def recorded(self, name):
        for entry in self.calls:
            if entry[0] == name:
                return entry[1], entry[2]
        raise AssertionError(f"{name} was never called; calls: {[call[0] for call in self.calls]}")

    def names(self):
        return [call[0] for call in self.calls]


class FakeManager:
    """Git worktree manager double recording every mutation it is asked for."""

    def __init__(self, worktree, *, changed=True, merge_error=None, remove_error=None,
                 create_error=None):
        self.worktree = worktree
        self.changed = changed
        self.merge_error = merge_error
        self.remove_error = remove_error
        self.create_error = create_error
        self.created: list[tuple] = []
        self.commits: list[tuple] = []
        self.merges: list[Worktree] = []
        self.removals: list[Worktree] = []

    def create(self, directory, task_name):
        self.created.append((directory, task_name))
        if self.create_error is not None:
            raise self.create_error
        return self.worktree

    def commit_changes(self, worktree, message):
        self.commits.append((worktree, message))
        return self.changed

    def merge(self, worktree):
        self.merges.append(worktree)
        if self.merge_error is not None:
            raise self.merge_error

    def remove(self, worktree):
        self.removals.append(worktree)
        if self.remove_error is not None:
            raise self.remove_error


class FakeEngine:
    """Workflow engine double; async methods match the real signatures.

    Workflows are created through the injected store so provenance rows and
    debugging tasks satisfy the same foreign keys the real path does.
    """

    def __init__(self, state, *, ready=True, summary=None, error=None, reasons=(),
                 implementation_status=TaskStatus.PASSED):
        self.state = state
        self.ready = ready
        self.summary = summary or ("READY" if ready else "; ".join(reasons))
        self.error = error
        self.reasons = reasons
        self.implementation_status = implementation_status
        self.high_level: list[tuple] = []
        self.created: list[tuple] = []
        self.executed: list[tuple] = []
        self.workflow_id: str | None = None

    def _open(self, description):
        """Persist the workflow plus its implementation task, as the engine would."""
        self.workflow_id = self.state.store.create_workflow(description)
        self.state.store.add_task(Task(f"Implement: {description}", "implementation",
                                       self.workflow_id, status=self.implementation_status))
        return self.workflow_id

    async def run_high_level(self, description, working_directory, cancel_event=None):
        self.high_level.append((description, working_directory))
        if self.error is not None:
            raise self.error
        return WorkflowResult(self._open(description), self.ready, self.summary)

    def create_workflow(self, description, specifications):
        self.created.append((description, specifications))
        self.workflow_id = self.state.store.create_workflow(description)
        return self.workflow_id, []

    async def execute(self, workflow_id, working_directory, cancel_event=None):
        self.executed.append((workflow_id, working_directory))

    def workflow_readiness(self, workflow_id, **kwargs):
        return WorkflowReadiness(workflow_id, self.ready, True, True, self.reasons)


class FakeLogs:
    def __init__(self, paths=()):
        self.paths = list(paths)
        self.queries: list[str | None] = []

    def list_logs(self, task_id=None):
        self.queries.append(task_id)
        return list(self.paths)


class FakeRegistry:
    def __init__(self, agents=None):
        self.agents = agents or {}
        self.requested: list[str] = []

    def get(self, name):
        self.requested.append(name)
        try:
            return self.agents[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

    def detect(self):
        return dict(self.agents)


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    async def run_agent(self, agent, prompt, directory):
        self.calls.append((agent, prompt, directory))
        if self.error is not None:
            raise self.error
        return self.result


def _raise_value_error(*args, **kwargs):
    raise ValueError("Event query limit and offset must be non-negative integers.")


class _Agent:
    """Minimal stand-in for a detected agent row."""

    def __init__(self, status, health_detail=None, health=AgentHealth.AVAILABLE):
        self.status = status
        self.health_detail = health_detail
        self.health = health


class _ServiceBundleCase(unittest.TestCase):
    """Builds an injected bundle over an in-memory store and a temp root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = SpyState()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.state.store.close)

    def services(self, **overrides) -> CliServices:
        settings = {"config": _config(), "state_root": self.root / ".agentops",
                    "state": self.state}
        settings.update(overrides)
        return CliServices(**settings)

    def invoke(self, argv, services=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv, services=services or self.services())
        return code, output.getvalue()


class ParserTests(unittest.TestCase):
    def test_missing_command_exits_with_usage_error(self):
        with self.assertRaises(SystemExit) as raised:
            main([])
        self.assertEqual(raised.exception.code, 2)

    def test_unknown_command_exits_with_usage_error(self):
        with self.assertRaises(SystemExit) as raised:
            main(["teleport"])
        self.assertEqual(raised.exception.code, 2)

    def test_unknown_option_exits_with_usage_error(self):
        with self.assertRaises(SystemExit) as raised:
            main(["status", "--turbo"])
        self.assertEqual(raised.exception.code, 2)

    def test_non_integer_limit_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit) as raised:
            main(["runs", "--limit", "many"])
        self.assertEqual(raised.exception.code, 2)

    def test_version_flag_prints_version_and_exits_zero(self):
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with redirect_stdout(output):
                main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("agentops", output.getvalue())

    def test_run_requires_agent_and_prompt(self):
        with self.assertRaises(SystemExit) as raised:
            main(["run"])
        self.assertEqual(raised.exception.code, 2)

    def test_workflow_file_defaults_to_the_current_directory(self):
        args = build_parser().parse_args(["workflow", "flow.json"])
        self.assertEqual(args.cwd, Path.cwd())


class ServiceBundleTests(_ServiceBundleCase):
    def test_injected_collaborators_are_used_verbatim(self):
        registry, manager, logs = FakeRegistry(), FakeManager(self._worktree()), FakeLogs()
        services = self.services(state=self.state, registry=registry, manager=manager, logs=logs)
        self.assertIs(services.state, self.state)
        self.assertIs(services.registry, registry)
        self.assertIs(services.manager, manager)
        self.assertIs(services.logs, logs)

    def test_state_is_constructed_under_the_state_root_when_not_injected(self):
        services = CliServices(config=_config(), state_root=self.root / ".agentops")
        self.addCleanup(services.close)
        self.assertEqual(services.state.database_path,
                         str(self.root / ".agentops" / "state.sqlite"))

    def test_logs_state_and_artifact_store_share_one_root(self):
        services = self.services()
        self.assertEqual(services.logs.root, self.root / ".agentops" / "logs")
        self.assertEqual(services.artifact_store.root, self.root / ".agentops" / "artifacts")
        services.close()

    def test_collaborators_are_built_once_and_reused(self):
        services = self.services()
        self.assertIs(services.state, services.state)
        self.assertIs(services.registry, services.registry)
        self.assertIs(services.engine, services.engine)
        services.close()

    def test_close_leaves_an_injected_store_open_for_the_caller(self):
        services = self.services(state=self.state)
        services.close()
        self.assertEqual(self.state.store.count_workflows(), 0)

    def test_engine_wires_verification_profile_configuration(self):
        profile = VerificationProfile(name="fast")
        config = _config(verification_profiles={"fast": profile},
                         default_verification_profile="fast",
                         verification_commands=(("pytest",),))
        services = self.services(state=self.state, config=config)
        engine = services.engine
        self.assertIs(engine.verification_kernel, engine.verification_kernel)
        self.assertEqual(engine.verification_kernel.resolve_profile().name, "fast")
        self.assertEqual(engine.verifier.commands, (("pytest",),))
        services.close()

    def test_state_root_requires_args_or_an_explicit_root(self):
        services = CliServices(config=_config())
        with self.assertRaises(ValueError):
            services.state_root

    def test_state_root_is_required_before_any_collaborator_is_built(self):
        services = self.services()
        self.assertEqual(services.state_root, self.root / ".agentops")

    def test_build_services_loads_the_requested_configuration(self):
        config_path = self.root / "agents.json"
        config_path.write_text(json.dumps({
            "agents": {"demo": {"command": "demo-cli", "args": ["{prompt}"]}},
        }), encoding="utf-8")
        args = build_parser().parse_args(["--config", str(config_path), "status"])
        services = _build_services(args)
        self.assertEqual(list(services.config.agents), ["demo"])

    def test_build_services_raises_for_a_missing_configuration_file(self):
        args = build_parser().parse_args(["--config", str(self.root / "nope.json"), "status"])
        with self.assertRaises(OSError):
            _build_services(args)

    def test_main_reports_a_configuration_error_before_touching_services(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--config", str(self.root / "nope.json"), "status"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR", output.getvalue())

    def test_resolve_state_base_falls_back_outside_a_repository(self):
        self.assertEqual(_resolve_state_base(self.root), self.root)

    def _worktree(self):
        return Worktree(repository=self.root, path=self.root / "wt",
                        branch="agentops/demo", base_branch="main", base_commit="a" * 40)


class StatusCommandTests(_ServiceBundleCase):
    def _seed(self):
        store = self.state.store
        workflow_id = store.create_workflow("ship the change")
        implementation = store.add_task(Task("Implement: ship", "implementation", workflow_id,
                                             status=TaskStatus.PASSED))
        run = store.create_agent_run(AgentRunContext(
            agent="demo", workflow_id=workflow_id, task_id=implementation.id))
        store.finish_agent_run(run.id, AgentRunOutcome(
            status=AgentRunStatus.COMPLETED, exit_code=0, duration_seconds=1.5))
        verification = store.create_verification_run(workflow_id, implementation.id,
                                                     VerificationProfile(name="fast"))
        store.finish_verification_run(
            verification.id, VerificationRunStatus.COMPLETED, VerificationReportStatus.PASSED,
            1, 1, 0, 0, 0, 0.2)
        store.create_failure(Failure(
            id="failure-1", workflow_id=workflow_id, task_id=implementation.id, agent_run_id=None,
            source=FailureSource.AGENT, category=FailureCategory.PROCESS_ERROR,
            severity=FailureSeverity.HIGH, retryable=True, repairable=True,
            recommended_action=RepairAction.RETRY_SAME_AGENT,
            recovery_state=RecoveryState.RECOVERED_FAILED))
        store.record_worktree_ref(WorktreeRef(
            id="ref-1", workflow_id=workflow_id, path=str(self.root / "wt"),
            branch="agentops/demo", base_branch="main", base_commit="b" * 40))
        return workflow_id, implementation.id

    def test_empty_state_reports_no_workflows(self):
        code, output = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No persisted workflows.")

    def test_status_summarizes_tasks_runs_verification_failures_and_provenance(self):
        workflow_id, task_id = self._seed()
        code, output = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertIn(f"{workflow_id}  pending  ship the change", output)
        self.assertIn("passed   implementation   Implement: ship", output)
        self.assertIn(f"run completed  demo           task={task_id} attempt=1", output)
        self.assertIn(f"verification passed     fast           task={task_id}", output)
        self.assertIn(f"failure PROCESS_ERROR        retry_same_agent       task={task_id}",
                      output)
        self.assertIn(f"worktree agentops/demo base=main@{'b' * 12} path={self.root / 'wt'}",
                      output)

    def test_status_queries_the_newest_workflow_with_documented_limits(self):
        workflow_id, _ = self._seed()
        self.invoke(["status"])
        for name, expected_limit in (("list_agent_runs", 50), ("list_verification_runs", 20),
                                     ("list_failures", 10)):
            args, kwargs = self.state.recorded(name)
            self.assertEqual(args[0], workflow_id)
            self.assertEqual(kwargs["limit"], expected_limit)

    def test_status_leaves_the_injected_store_usable(self):
        self._seed()
        code, _ = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(self.state.store.count_workflows(), 1)


class RunsCommandTests(_ServiceBundleCase):
    def _workflow_task(self, description="run something"):
        workflow_id = self.state.store.create_workflow(description)
        task = self.state.store.add_task(Task("Do the work", "implementation", workflow_id))
        return workflow_id, task.id

    def _seed(self):
        workflow_id, task_id = self._workflow_task()
        run = self.state.store.create_agent_run(AgentRunContext(
            agent="demo", workflow_id=workflow_id, task_id=task_id))
        self.state.store.finish_agent_run(run.id, AgentRunOutcome(
            status=AgentRunStatus.FAILED, exit_code=1, duration_seconds=2.0))
        return workflow_id, task_id, run

    def test_no_runs_reports_an_empty_listing(self):
        code, output = self.invoke(["runs"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No persisted agent runs.")

    def test_run_rows_show_status_agent_attempt_and_exit_code(self):
        _, task_id, run = self._seed()
        code, output = self.invoke(["runs"])
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  failed  demo  task={task_id} attempt=1 exit=1 duration=2.0s", output)

    def test_missing_duration_renders_as_a_placeholder(self):
        workflow_id, _ = self._workflow_task()
        self.state.store.create_agent_run(AgentRunContext(agent="demo", workflow_id=workflow_id))
        _, output = self.invoke(["runs"])
        self.assertIn("duration=-", output)

    def test_filters_and_paging_are_forwarded_to_the_store(self):
        workflow_id, task_id = self._seed()[:2]
        self.invoke(["runs", "--workflow", workflow_id, "--task", task_id,
                     "--status", "failed", "--limit", "5", "--offset", "2"])
        args, _ = self.state.recorded("list_agent_runs")
        self.assertEqual(args[0], workflow_id)
        self.assertEqual(args[1], task_id)
        self.assertIs(args[2], AgentRunStatus.FAILED)
        self.assertEqual(args[3:], (5, 2))

    def test_unknown_status_is_reported_as_a_usage_level_error(self):
        code, output = self.invoke(["runs", "--status", "confused"])
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("ERROR:"))
        self.assertNotIn("list_agent_runs", self.state.names())


class EventsCommandTests(_ServiceBundleCase):
    def _seed(self):
        self.state.store.record_typed_event(Event(
            workflow_id="wf", task_id="task-1",
            type=EventType.AGENT_FINISHED, severity=EventSeverity.WARNING,
            message="agent finished slowly"))

    def test_no_events_reports_an_empty_timeline(self):
        code, output = self.invoke(["events"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No timeline events.")

    def test_events_print_timestamp_type_severity_scope_and_message(self):
        self._seed()
        code, output = self.invoke(["events"])
        self.assertEqual(code, 0)
        self.assertIn("agent.finished", output)
        self.assertIn("warning", output)
        self.assertIn("workflow=wf task=task-1 run=-", output)
        self.assertIn("agent finished slowly", output)

    def test_every_filter_is_forwarded_to_the_timeline_query(self):
        self._seed()
        self.invoke(["events", "--workflow", "wf", "--task", "task-1", "--run", "run-1",
                     "--type", "agent.finished", "--limit", "7", "--offset", "3"])
        args, _ = self.state.recorded("query_events")
        self.assertEqual(args, ("wf", "task-1", "run-1", "agent.finished", 7, 3))

    def test_type_filter_narrows_the_timeline(self):
        self._seed()
        _, output = self.invoke(["events", "--type", "task.claimed"])
        self.assertEqual(output.strip(), "No timeline events.")

    def test_store_errors_are_not_swallowed_by_the_events_listing(self):
        self.state.store.query_events = _raise_value_error
        self.addCleanup(setattr, self.state.store, "query_events",
                        self.state.store.query_events)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                main(["events"], services=self.services())


class ArtifactsCommandTests(_ServiceBundleCase):
    def _store_with(self, count, workflow_id="wf"):
        store = ArtifactStore(self.root / ".agentops" / "artifacts")
        created = []
        for index in range(count):
            artifact = store.write(f"body-{index}", name=f"artifact-{index}",
                                   workflow_id=workflow_id)
            self.state.store.create_artifact_record(artifact)
            created.append(artifact)
        return store, created

    def test_no_artifacts_reports_an_empty_listing(self):
        code, output = self.invoke(["artifacts"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No stored artifacts.")

    def test_listing_reports_kind_name_size_digest_and_workflow(self):
        _, created = self._store_with(1)
        code, output = self.invoke(["artifacts"])
        self.assertEqual(code, 0)
        self.assertIn(created[0].id, output)
        self.assertIn("custom", output)
        self.assertIn("sha256=" + created[0].sha256[:12], output)
        self.assertIn("workflow=wf", output)

    def test_filters_are_forwarded_to_the_artifact_query(self):
        self._store_with(2)
        self.invoke(["artifacts", "--workflow", "wf", "--task", "task-1",
                     "--kind", "plans", "--limit", "4", "--offset", "1"])
        args, _ = self.state.recorded("list_artifacts")
        self.assertEqual(args, ("wf", "task-1", "plans", 4, 1))

    def test_show_prints_stored_content(self):
        _, created = self._store_with(1)
        code, output = self.invoke(["artifacts", "--show", created[0].id])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "body-0")

    def test_show_of_an_unknown_artifact_is_a_usage_level_error(self):
        code, output = self.invoke(["artifacts", "--show", "does-not-exist"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR: unknown artifact does-not-exist", output)

    def test_show_reports_a_corrupted_or_missing_payload(self):
        _, created = self._store_with(1)
        (self.root / ".agentops" / "artifacts" / created[0].rel_path).unlink()
        code, output = self.invoke(["artifacts", "--show", created[0].id])
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("ERROR:"))

    def test_prune_deletes_files_and_metadata_rows(self):
        store, created = self._store_with(3)
        code, output = self.invoke(["artifacts", "--prune-keep", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "Pruned 2 artifact(s).")
        self.assertEqual(len(self.state.store.list_artifacts()), 1)
        kept = self.state.store.list_artifacts()[0]
        self.assertEqual(store.find_orphans([kept.rel_path]), [])

    def test_prune_keeping_everything_deletes_nothing(self):
        self._store_with(2)
        code, output = self.invoke(["artifacts", "--prune-keep", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "Pruned 2 artifact(s).")
        self.assertEqual(self.state.store.list_artifacts(), [])

    def test_prune_zero_deletes_every_artifact_and_its_row(self):
        self._store_with(1)
        code, output = self.invoke(["artifacts", "--prune-keep", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "Pruned 0 artifact(s).")
        self.assertEqual(len(self.state.store.list_artifacts()), 1)


class VerifyCommandTests(_ServiceBundleCase):
    def _scope(self, description="verify something"):
        workflow_id = self.state.store.create_workflow(description)
        task_id = self.state.store.add_task(
            Task("Run checks", "verification", workflow_id)).id
        return workflow_id, task_id

    def _seed(self, *, with_report=True):
        workflow_id, task_id = self._scope()
        profile = VerificationProfile(name="fast")
        run = self.state.store.create_verification_run(workflow_id, task_id, profile)
        check = self.state.store.create_verification_check(
            run.id, workflow_id, task_id, profile.name, "unit", "tests", ("pytest",),
            None, None, True, "sequential")
        self.state.store.start_verification_check(check.id)
        self.state.store.finish_verification_check(
            check.id, VerificationCheckStatus.PASSED, exit_code=0, duration_seconds=0.3)
        self.state.store.finish_verification_run(
            run.id, VerificationRunStatus.COMPLETED, VerificationReportStatus.PASSED,
            1, 1, 0, 0, 0, 0.4)
        if with_report:
            self.state.store.create_verification_report(VerificationReport(
                id="report-1", run_id=run.id, workflow_id=workflow_id, task_id=task_id,
                profile_name=profile.name, total_checks=1, passed_checks=1, failed_checks=0,
                skipped_checks=0, required_failures=0, duration_seconds=0.4,
                overall_status=VerificationReportStatus.PASSED))
        return workflow_id, task_id, run

    def test_no_runs_reports_an_empty_listing(self):
        code, output = self.invoke(["verify"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No persisted verification runs.")

    def test_run_listing_reports_profile_overall_status_and_check_counts(self):
        _, task_id, run = self._seed()
        code, output = self.invoke(["verify"])
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  passed  profile=fast  task={task_id} passed=1/1", output)
        self.assertIn("required_failures=0", output)

    def test_pending_run_without_a_report_falls_back_to_its_run_status(self):
        workflow_id, task_id = self._scope()
        run = self.state.store.create_verification_run(workflow_id, task_id,
                                                       VerificationProfile(name="fast"))
        code, output = self.invoke(["verify"])
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  pending  profile=fast  task={task_id} passed=0/0", output)

    def test_listing_filters_are_forwarded_to_the_store(self):
        workflow_id, task_id = self._seed()[:2]
        self.invoke(["verify", "--workflow", workflow_id, "--task", task_id,
                     "--limit", "6", "--offset", "2"])
        args, kwargs = self.state.recorded("list_verification_runs")
        self.assertEqual(args, (workflow_id, task_id))
        self.assertEqual(kwargs, {"limit": 6, "offset": 2})

    def test_run_detail_prints_the_report_and_every_check(self):
        _, _, run = self._seed()
        code, output = self.invoke(["verify", "--run", run.id])
        self.assertEqual(code, 0)
        self.assertIn(f"{run.id}  passed  profile=fast  passed=1/1", output)
        self.assertIn("unit", output)
        self.assertIn("exit=0", output)
        self.assertIn("required=True", output)
        self.assertIn("reason=-", output)

    def test_unknown_run_is_a_usage_level_error(self):
        code, output = self.invoke(["verify", "--run", "no-such-run"])
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("ERROR:"))
        self.assertIn("Unknown verification run", output)

    def test_run_without_a_stored_report_is_a_usage_level_error(self):
        _, _, run = self._seed(with_report=False)
        code, output = self.invoke(["verify", "--run", run.id])
        self.assertEqual(code, 2)
        self.assertIn("Unknown verification report", output)

    def test_negative_limit_is_a_usage_level_error(self):
        code, output = self.invoke(["verify", "--limit", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("limit and offset", output)


class FailuresCommandTests(_ServiceBundleCase):
    def _seed(self):
        workflow_id = self.state.store.create_workflow("fail something")
        task_id = self.state.store.add_task(
            Task("Do the work", "implementation", workflow_id)).id
        return self.state.store.create_failure(Failure(
            id="failure-1", workflow_id=workflow_id, task_id=task_id, agent_run_id=None,
            source=FailureSource.VERIFICATION, category=FailureCategory.TEST_FAILURE,
            severity=FailureSeverity.HIGH, retryable=False, repairable=True,
            recommended_action=RepairAction.REPAIR_IMPLEMENTATION,
            recovery_state=RecoveryState.RECOVERED_FAILED))

    def test_no_failures_reports_an_empty_listing(self):
        code, output = self.invoke(["failures"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No persisted failures.")

    def test_failure_rows_report_classification_and_recovery_state(self):
        failure = self._seed()
        code, output = self.invoke(["failures"])
        self.assertEqual(code, 0)
        self.assertIn(f"{failure.id}  TEST_FAILURE  HIGH", output)
        self.assertIn("action=repair_implementation", output)
        self.assertIn("retryable=False repairable=True", output)
        self.assertIn("recovery=recovered_failed", output)

    def test_recovery_state_is_optional(self):
        self._seed()
        self.state.store.connection.execute("UPDATE failures SET recovery_state=NULL")
        _, output = self.invoke(["failures"])
        self.assertIn("recovery=-", output)

    def test_category_filter_narrows_the_listing(self):
        self._seed()
        code, output = self.invoke(["failures", "--category", "AGENT_ERROR"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "No persisted failures.")

    def test_filters_are_forwarded_to_the_failure_query(self):
        self._seed()
        self.invoke(["failures", "--workflow", "wf", "--task", "task-1",
                     "--category", "TEST_FAILURE", "--limit", "3", "--offset", "1"])
        args, _ = self.state.recorded("list_failures")
        self.assertEqual(args, ("wf", "task-1", "TEST_FAILURE", 3, 1))

    def test_negative_limit_is_a_usage_level_error(self):
        code, output = self.invoke(["failures", "--limit", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("limit and offset", output)


class RecoverCommandTests(_ServiceBundleCase):
    def test_recover_reports_the_count_of_each_recovered_class(self):
        code, output = self.invoke(["recover"])
        self.assertEqual(code, 0)
        self.assertIn("Recovered agent_runs=0 verification_runs=0 tasks=0.", output)
        self.assertIn("Interrupted work is marked failed (never success without evidence);",
                      output)
        self.assertIn("worktrees preserved.", output)
        self.assertIn("recover_all", self.state.names())

    def test_recover_terminates_a_stranded_agent_run(self):
        workflow_id = self.state.store.create_workflow("stranded")
        run = self.state.store.create_agent_run(AgentRunContext(agent="demo",
                                                                workflow_id=workflow_id))
        self.state.store.start_agent_run(run.id)
        self.state.store.mark_agent_run_running(run.id)
        code, output = self.invoke(["recover"])
        self.assertEqual(code, 0)
        self.assertIn("agent_runs=1", output)
        recovered = self.state.store.get_agent_run(run.id)
        self.assertIs(recovered.status, AgentRunStatus.TERMINATED)
        self.assertTrue(recovered.terminated)

    def test_recover_fails_a_stranded_running_task(self):
        workflow_id = self.state.store.create_workflow("stranded")
        task = self.state.store.add_task(Task("Do the work", "implementation", workflow_id))
        self.state.store.claim_task(task.id)
        code, output = self.invoke(["recover"])
        self.assertEqual(code, 0)
        self.assertIn("tasks=1", output)
        self.assertIs(self.state.store.get_task(task.id).status, TaskStatus.FAILED)


class LogsCommandTests(_ServiceBundleCase):
    def test_log_paths_are_listed_in_order(self):
        logs = FakeLogs(["a.log", "b.log"])
        code, output = self.invoke(["logs"], self.services(logs=logs))
        self.assertEqual(code, 0)
        self.assertEqual(output.split(), ["a.log", "b.log"])
        self.assertEqual(logs.queries, [None])

    def test_tail_truncates_the_log_listing(self):
        logs = FakeLogs([f"{index}.log" for index in range(5)])
        _, output = self.invoke(["logs", "--tail", "2"], self.services(logs=logs))
        self.assertEqual(output.split(), ["0.log", "1.log"])

    def test_task_filter_is_forwarded_to_the_log_manager(self):
        logs = FakeLogs(["a.log"])
        self.invoke(["logs", "--task", "task-1"], self.services(logs=logs))
        self.assertEqual(logs.queries, ["task-1"])


class AgentsCommandTests(_ServiceBundleCase):
    def test_detected_agents_are_listed_with_health_detail(self):
        registry = FakeRegistry({
            "codex": _Agent("ONLINE"),
            "fcc-claude": _Agent("UNHEALTHY", "proxy is unreachable"),
            "legacy": _Agent("DISABLED"),
        })
        code, output = self.invoke(["agents"], self.services(registry=registry))
        self.assertEqual(code, 0)
        self.assertIn("codex", output)
        self.assertIn("ONLINE", output)
        self.assertIn("UNHEALTHY", output)
        self.assertIn("(proxy is unreachable)", output)
        self.assertIn("DISABLED", output)

    def test_agents_listing_never_opens_the_state_database(self):
        services = self.services(registry=FakeRegistry())
        code, _ = self.invoke(["agents"], services)
        self.assertEqual(code, 0)
        self.assertEqual(self.state.names(), [])
        self.assertFalse((self.root / ".agentops" / "state.sqlite").exists())

    def test_empty_registry_produces_no_rows(self):
        code, output = self.invoke(["agents"], self.services(registry=FakeRegistry()))
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "")

    def test_agents_command_reads_the_bundled_configuration_in_production(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["agents"])
        self.assertEqual(code, 0)
        self.assertIn("fcc-claude", output.getvalue())
        self.assertIn("codex", output.getvalue())


class RunCommandTests(_ServiceBundleCase):
    def _result(self, **overrides):
        settings = {"agent": "demo", "command": ("demo-cli",), "exit_code": 0,
                    "stdout": "agent said hello", "stderr": "", "duration_seconds": 1.25,
                    "timed_out": False, "log_path": Path("logs/demo.meta.log"), "run_id": "run-1"}
        settings.update(overrides)
        return RunResult(**settings)

    def _services(self, registry, runner):
        return self.services(state=self.state, registry=registry, runner=runner)

    def test_successful_run_reports_outcome_run_log_and_output(self):
        registry = FakeRegistry({"demo": object()})
        runner = FakeRunner(self._result())
        code, output = self.invoke(["run", "demo", "fix the bug"], self._services(registry, runner))
        self.assertEqual(code, 0)
        self.assertIn("[demo] PASSED (1.2s)", output)
        self.assertIn("run: run-1", output)
        self.assertIn("log: logs/demo.meta.log", output)
        self.assertIn("agent said hello", output)

    def test_agent_prompt_and_working_directory_are_forwarded_to_the_runner(self):
        agent = object()
        registry = FakeRegistry({"demo": agent})
        runner = FakeRunner(self._result())
        self.invoke(["run", "demo", "fix the bug"], self._services(registry, runner))
        self.assertEqual(registry.requested, ["demo"])
        self.assertEqual(runner.calls, [(agent, "fix the bug", Path.cwd())])

    def test_failed_run_returns_one_and_prints_stderr(self):
        registry = FakeRegistry({"demo": object()})
        runner = FakeRunner(self._result(exit_code=2, stdout="", stderr="boom"))
        code, output = self.invoke(["run", "demo", "prompt"], self._services(registry, runner))
        self.assertEqual(code, 1)
        self.assertIn("[demo] FAILED (1.2s)", output)
        self.assertIn("boom", output)

    def test_timed_out_run_is_a_failure_even_with_a_zero_exit_code(self):
        registry = FakeRegistry({"demo": object()})
        runner = FakeRunner(self._result(timed_out=True, exit_code=0, stdout="", stderr=""))
        code, output = self.invoke(["run", "demo", "prompt"], self._services(registry, runner))
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)

    def test_unknown_agent_is_reported_without_running_anything(self):
        registry = FakeRegistry({})
        runner = FakeRunner(self._result())
        code, output = self.invoke(["run", "ghost", "prompt"], self._services(registry, runner))
        self.assertEqual(code, 1)
        self.assertIn("Unknown agent: ghost", output)
        self.assertTrue(output.startswith("ERROR:"))
        self.assertEqual(runner.calls, [])

    def test_unknown_agent_still_creates_and_closes_the_state_database(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        services = CliServices(config=_config(), state_root=Path(directory) / ".agentops",
                               registry=FakeRegistry({}))
        code, _ = self.invoke(["run", "ghost", "prompt"], services)
        self.assertEqual(code, 1)
        self.assertTrue((Path(directory) / ".agentops" / "state.sqlite").exists())

    def test_execution_failure_is_reported_as_a_run_error(self):
        registry = FakeRegistry({"demo": object()})
        runner = FakeRunner(error=OSError("spawn failed"))
        code, output = self.invoke(["run", "demo", "prompt"], self._services(registry, runner))
        self.assertEqual(code, 1)
        self.assertIn("ERROR: spawn failed", output)
        self.assertEqual(len(runner.calls), 1)


class GuiCommandTests(_ServiceBundleCase):
    def test_missing_desktop_dependency_is_reported(self):
        output = io.StringIO()
        with patch.dict("sys.modules", {"agentops.gui": None}):
            with redirect_stdout(output):
                code = main(["gui"])
        self.assertEqual(code, 1)
        self.assertIn("GUI dependencies are unavailable", output.getvalue())

    def test_launch_failure_is_reported(self):
        import agentops.gui

        with patch.object(agentops.gui, "main", side_effect=RuntimeError("no display")):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["gui"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: no display", output.getvalue())

    def test_successful_launch_receives_the_requested_config(self):
        import agentops.gui

        with patch.object(agentops.gui, "main") as launch:
            code = main(["--config", "custom.yaml", "gui"])
        self.assertEqual(code, 0)
        launch.assert_called_once_with(Path("custom.yaml"))


class WorkflowCommandTests(_ServiceBundleCase):
    def _worktree(self):
        return Worktree(repository=self.root, path=self.root / "wt",
                        branch="agentops/demo", base_branch="main", base_commit="c" * 40)

    def _bundle(self, *, engine=None, manager=None):
        return self.services(
            state=self.state,
            manager=manager or FakeManager(self._worktree()),
            engine=engine or FakeEngine(self.state))

    def test_ready_workflow_commits_merges_and_removes_the_worktree(self):
        manager, engine = FakeManager(self._worktree()), FakeEngine(self.state)
        code, output = self.invoke(["task", "ship it"], self._bundle(engine=engine, manager=manager))
        self.assertEqual(code, 0)
        self.assertIn("RESULT: READY", output)
        self.assertIn(f"workflow: {engine.workflow_id}", output)
        self.assertIn("merged worktree changes", output)
        self.assertEqual([message for _, message in manager.commits], ["agentops: ship it"])
        self.assertEqual(manager.merges, [self._worktree()])
        self.assertEqual(manager.removals, [self._worktree()])
        self.assertEqual(engine.high_level, [("ship it", self._worktree().path)])

    def test_worktree_provenance_is_persisted_for_later_merge_validation(self):
        self.invoke(["task", "ship it"], self._bundle())
        ref = self.state.store.get_worktree_ref(self.state.store.latest_workflow().id)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.branch, "agentops/demo")
        self.assertEqual(ref.base_commit, "c" * 40)

    def test_unready_workflow_still_merges_a_passed_implementation(self):
        engine = FakeEngine(self.state, ready=False, reasons=("no passed review task",))
        manager = FakeManager(self._worktree())
        code, output = self.invoke(["task", "ship it"], self._bundle(engine=engine, manager=manager))
        self.assertEqual(code, 1)
        self.assertIn("RESULT: no passed review task", output)
        self.assertIn("merged worktree changes", output)
        self.assertEqual(len(manager.merges), 1)
        self.assertEqual(manager.removals, [])
        self.assertIn("preserved", output)

    def test_unready_and_unimplemented_work_is_never_merged_or_removed(self):
        engine = FakeEngine(self.state, ready=False, reasons=("verification failed",),
                            implementation_status=TaskStatus.FAILED)
        manager = FakeManager(self._worktree())
        code, output = self.invoke(["task", "ship it"], self._bundle(engine=engine, manager=manager))
        self.assertEqual(code, 1)
        self.assertIn("no worktree changes to merge", output)
        self.assertEqual(manager.commits, [])
        self.assertEqual(manager.merges, [])
        self.assertEqual(manager.removals, [])
        self.assertIn("preserved", output)

    def test_clean_worktree_reports_nothing_to_merge(self):
        manager = FakeManager(self._worktree(), changed=False)
        code, output = self.invoke(["task", "ship it"], self._bundle(manager=manager))
        self.assertEqual(code, 0)
        self.assertIn("no worktree changes to merge", output)
        self.assertEqual(manager.merges, [])
        self.assertEqual(manager.removals, [self._worktree()])

    def test_merge_conflict_creates_a_debugging_task_and_preserves_the_worktree(self):
        manager = FakeManager(self._worktree(), merge_error=GitError("Automatic merge failed"))
        code, output = self.invoke(["task", "ship it"], self._bundle(manager=manager))
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT: Automatic merge failed", output)
        self.assertIn("worktree is preserved", output)
        self.assertEqual(manager.removals, [])
        debugging = [task for task in self.state.store.list_tasks() if task.role == "debugging"]
        self.assertEqual(len(debugging), 1)
        self.assertIn("merge conflict", debugging[0].description.lower())

    def test_worktree_creation_failure_is_reported_without_touching_state(self):
        manager = FakeManager(self._worktree(), create_error=GitError("Not a Git repository"))
        code, output = self.invoke(["task", "ship it"], self._bundle(manager=manager))
        self.assertEqual(code, 1)
        self.assertIn("ERROR: Not a Git repository", output)
        self.assertEqual(self.state.store.count_workflows(), 0)
        self.assertEqual(manager.commits, [])

    def test_engine_failure_is_reported_and_the_worktree_survives(self):
        engine = FakeEngine(self.state, error=RuntimeError("agent CLI is missing"))
        manager = FakeManager(self._worktree())
        code, output = self.invoke(["task", "ship it"], self._bundle(engine=engine, manager=manager))
        self.assertEqual(code, 1)
        self.assertIn("ERROR: agent CLI is missing", output)
        self.assertIn("preserved", output)
        self.assertEqual(manager.removals, [])

    def test_worktree_removal_failure_is_reported_but_the_run_still_succeeds(self):
        manager = FakeManager(self._worktree(), remove_error=GitError("worktree is locked"))
        code, output = self.invoke(["task", "ship it"], self._bundle(manager=manager))
        self.assertEqual(code, 0)
        self.assertIn(f"Worktree preserved at {self._worktree().path}: worktree is locked", output)

    def test_custom_dag_file_drives_create_workflow_execute_and_readiness(self):
        engine = FakeEngine(self.state, ready=False,
                            reasons=("no passed+verified verification task",))
        path = self.root / "flow.json"
        path.write_text(json.dumps({
            "description": "custom flow",
            "tasks": [{"id": "plan", "role": "planning", "description": "plan it"}],
        }), encoding="utf-8")
        code, output = self.invoke(["workflow", str(path)], self._bundle(engine=engine))
        self.assertEqual(code, 1)
        self.assertEqual(engine.created, [("custom flow", [{"id": "plan", "role": "planning",
                                                            "description": "plan it"}])])
        self.assertEqual(engine.executed, [(engine.workflow_id, self._worktree().path)])
        self.assertEqual(engine.high_level, [])
        self.assertIn("RESULT: no passed+verified verification task", output)

    def test_ready_custom_dag_still_merges_and_removes_the_worktree(self):
        engine = FakeEngine(self.state)
        path = self.root / "flow.json"
        path.write_text(json.dumps({"description": "custom flow", "tasks": [
            {"id": "plan", "role": "planning", "description": "plan it"}]}),
            encoding="utf-8")
        manager = FakeManager(self._worktree())
        code, output = self.invoke(["workflow", str(path)], self._bundle(engine=engine,
                                                                        manager=manager))
        self.assertEqual(code, 0)
        self.assertIn("RESULT: READY", output)
        self.assertEqual(len(manager.merges), 1)
        self.assertEqual(manager.removals, [self._worktree()])

    def test_workflow_file_without_a_description_is_a_usage_level_error(self):
        path = self.root / "flow.json"
        path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        code, output = self.invoke(["workflow", str(path)], self._bundle())
        self.assertEqual(code, 2)
        self.assertIn("requires a string 'description'", output)

    def test_blank_description_is_a_usage_level_error(self):
        path = self.root / "flow.json"
        path.write_text(json.dumps({"description": "   "}), encoding="utf-8")
        code, output = self.invoke(["workflow", str(path)], self._bundle())
        self.assertEqual(code, 2)
        self.assertIn("requires a string 'description'", output)

    def test_missing_workflow_file_is_a_usage_level_error(self):
        code, output = self.invoke(["workflow", str(self.root / "absent.json")], self._bundle())
        self.assertEqual(code, 2)
        self.assertIn("invalid workflow file", output)

    def test_unparseable_workflow_file_is_a_usage_level_error(self):
        path = self.root / "flow.json"
        path.write_text("{not json", encoding="utf-8")
        code, output = self.invoke(["workflow", str(path)], self._bundle())
        self.assertEqual(code, 2)
        self.assertIn("invalid workflow file", output)

    def test_non_list_task_specifications_are_rejected(self):
        path = self.root / "flow.json"
        path.write_text(json.dumps({"description": "custom flow", "tasks": {"id": "plan"}}),
                        encoding="utf-8")
        engine = FakeEngine(self.state)
        code, output = self.invoke(["workflow", str(path)], self._bundle(engine=engine))
        self.assertEqual(code, 2)
        self.assertIn("'tasks' must be a list of mappings", output)
        self.assertEqual(engine.created, [])

    def test_non_mapping_task_specifications_are_rejected(self):
        path = self.root / "flow.json"
        path.write_text(json.dumps({"description": "custom flow", "tasks": ["plan it"]}),
                        encoding="utf-8")
        engine = FakeEngine(self.state)
        code, output = self.invoke(["workflow", str(path)], self._bundle(engine=engine))
        self.assertEqual(code, 2)
        self.assertIn("'tasks' must be a list of mappings", output)
        self.assertEqual(engine.created, [])

    def test_cwd_is_forwarded_to_worktree_creation(self):
        manager = FakeManager(self._worktree())
        self.invoke(["task", "ship it", "--cwd", str(self.root)], self._bundle(manager=manager))
        self.assertEqual(manager.created, [(Path(self.root), "ship it")])

    def test_task_command_requires_a_repository_before_any_state_is_written(self):
        output = io.StringIO()
        with patch.object(cli_module.GitWorktreeManager, "repository_root",
                          side_effect=GitError("Not a Git repository")):
            with redirect_stdout(output):
                code = main(["task", "ship it"], services=None)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: Not a Git repository", output.getvalue())


if __name__ == "__main__":
    unittest.main()
