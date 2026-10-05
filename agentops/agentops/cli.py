"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .agent_run import AgentRunStatus, GitRunMetadataCollector
from .artifacts import ArtifactError, ArtifactStore
from .tasks import TaskStatus
from .config import AppConfig, _load_data, load_config
from .finalize import WorktreeFinalization, finalize_worktree, record_worktree_provenance
from .git import GitError, GitWorktreeManager
from .registry import AgentRegistry
from .logging import LogManager
from .persistence import DegradationRecorder, event_emitter
from .runner import AgentRunner
from .state import StateStore
from .verification import Verifier
from .verification_kernel import VerificationKernel
from .workflow import WorkflowEngine, WorkflowResult


def _implementation_passed(state, workflow_id: str) -> bool:
    """True when the implementation task itself reached PASSED.

    Used to decide whether delegate work should be committed and merged even
    though verification did not reach PASSED. A FAILED implementation is never
    merged; an UNVERIFIED verification is not by itself a reason to discard work.
    """
    try:
        tasks = state.list_tasks(workflow_id)
    except Exception:
        return False
    return any(task.role == "implementation" and task.status is TaskStatus.PASSED
               for task in tasks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentops", description="Local-first coding-agent orchestration")
    parser.add_argument("--config", type=Path, help="Path to YAML configuration")
    parser.add_argument("--version", action="version", version=f"agentops {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("agents", help="Detect configured coding-agent CLIs")
    subcommands.add_parser("gui", help="Launch the AgentOps desktop client")
    subcommands.add_parser("status", help="Show persistent workflow status")
    run = subcommands.add_parser("run", help="Run one installed agent")
    run.add_argument("agent")
    run.add_argument("prompt")
    runs = subcommands.add_parser("runs", help="Inspect persisted agent runs")
    runs.add_argument("--workflow")
    runs.add_argument("--task")
    runs.add_argument("--status")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--offset", type=int, default=0)
    task = subcommands.add_parser("task", help="Execute a high-level task workflow")
    task.add_argument("description")
    task.add_argument("--cwd", type=Path, default=Path.cwd())
    logs = subcommands.add_parser("logs", help="Inspect saved task and runner logs")
    logs.add_argument("--task")
    logs.add_argument("--tail", type=int, default=30)
    verify = subcommands.add_parser("verify", help="Inspect persisted verification runs and reports")
    verify.add_argument("--workflow")
    verify.add_argument("--run")
    verify.add_argument("--task")
    verify.add_argument("--limit", type=int, default=20)
    verify.add_argument("--offset", type=int, default=0)
    failures = subcommands.add_parser("failures", help="Inspect persisted failures")
    failures.add_argument("--workflow")
    failures.add_argument("--task")
    failures.add_argument("--category")
    failures.add_argument("--limit", type=int, default=20)
    failures.add_argument("--offset", type=int, default=0)
    subcommands.add_parser("recover", help="Recover interrupted agent runs, verification runs, and tasks")
    events = subcommands.add_parser("events", help="Query the durable execution timeline")
    events.add_argument("--workflow")
    events.add_argument("--task")
    events.add_argument("--run")
    events.add_argument("--type")
    events.add_argument("--limit", type=int, default=20)
    events.add_argument("--offset", type=int, default=0)
    artifacts = subcommands.add_parser("artifacts", help="Inspect stored workflow artifacts")
    artifacts.add_argument("--workflow")
    artifacts.add_argument("--task")
    artifacts.add_argument("--kind")
    artifacts.add_argument("--show")
    artifacts.add_argument("--prune-keep", type=int, default=None)
    artifacts.add_argument("--limit", type=int, default=20)
    artifacts.add_argument("--offset", type=int, default=0)
    workflow = subcommands.add_parser("workflow", help="Execute a YAML workflow file")
    workflow.add_argument("file", type=Path)
    workflow.add_argument("--cwd", type=Path, default=Path.cwd(),
                          help="Working directory of the target repository (defaults to cwd)")
    return parser


def _print_text(text: str) -> None:
    """Print agent output without crashing on consoles with narrow encodings.

    Windows consoles commonly use cp1252/cp437, while agent output may contain
    arbitrary Unicode. Fall back to encoding with replacement instead of
    raising UnicodeEncodeError after a successful run.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


def _resolve_state_base(directory: Path) -> Path:
    """Resolve the canonical repository root for state and worktree locations.

    Falls back to the given directory when it is not inside a Git repository,
    preserving the previous behaviour for non-Git usage (agents/run/logs).
    """
    try:
        return GitWorktreeManager().repository_root(directory)
    except GitError:
        return directory


def _state_root_for(args: argparse.Namespace) -> Path:
    """Resolve the `.agentops` directory for one parsed command line.

    Worktree commands require a Git repository and raise :class:`GitError`
    otherwise; every other command falls back to the previous behaviour of
    using the command's working directory.
    """
    command_base = getattr(args, "cwd", None) or Path.cwd()
    if args.command in ("task", "workflow"):
        # Worktrees require a Git repository, so resolve the canonical root
        # up front: state, logs, and worktree then share one location, and a
        # non-repository directory fails fast without stray .agentops files.
        return GitWorktreeManager().repository_root(command_base) / ".agentops"
    return _resolve_state_base(command_base) / ".agentops"


class CliServices:
    """Injectable collaborators for one CLI invocation.

    Every service is constructed on first use, so each command builds only what
    it needs: `agents` never opens the state database, `runs` never builds a
    workflow engine, and nothing spawns a subprocess before the command that
    needs it. Tests pass a bundle whose fields are fakes -- or an in-memory
    :class:`StateStore` -- to :func:`main` instead of letting the CLI construct
    real SQLite, Git, and process collaborators.
    """

    def __init__(self, config: AppConfig, args: argparse.Namespace | None = None, *,
                 state_root: Path | None = None, logs: LogManager | None = None,
                 state: StateStore | None = None, registry: AgentRegistry | None = None,
                 manager: GitWorktreeManager | None = None,
                 runner: AgentRunner | None = None,
                 engine: WorkflowEngine | None = None,
                 artifact_store: ArtifactStore | None = None):
        self.config = config
        self.args = args
        self._state_root = state_root
        self._logs = logs
        self._state = state
        self._registry = registry
        self._manager = manager
        self._runner = runner
        self._engine = engine
        self._artifact_store = artifact_store
        # An injected store belongs to the caller: closing it here would make
        # the bundle unusable for anything after the command returns.
        self._owns_state = state is None

    def close(self) -> None:
        """Close the state store, but only when this bundle constructed it."""
        if self._owns_state and self._state is not None:
            self._state.close()
            self._state = None

    @property
    def state_root(self) -> Path:
        """Canonical `.agentops` directory, resolved from `args` when needed."""
        if self._state_root is None:
            if self.args is None:
                raise ValueError("CliServices needs either state_root or args.")
            self._state_root = _state_root_for(self.args)
        return self._state_root

    def require_state_root(self) -> Path:
        """Force the up-front state-root resolution the CLI reports on.

        Resolving lazily keeps `agents` from probing Git, but every other
        command must surface a `GitError` here rather than from whichever
        collaborator happens to touch the filesystem first.
        """
        return self.state_root

    @property
    def logs(self) -> LogManager:
        if self._logs is None:
            self._logs = LogManager(self.state_root / "logs")
        return self._logs

    @property
    def state(self) -> StateStore:
        if self._state is None:
            self._state = StateStore(self.state_root / "state.sqlite")
        return self._state

    @property
    def registry(self) -> AgentRegistry:
        if self._registry is None:
            self._registry = AgentRegistry(self.config)
        return self._registry

    @property
    def manager(self) -> GitWorktreeManager:
        if self._manager is None:
            self._manager = GitWorktreeManager()
        return self._manager

    @property
    def artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = ArtifactStore(self.state_root / "artifacts")
        return self._artifact_store

    @property
    def runner(self) -> AgentRunner:
        """Bare runner for the single-agent `run` command."""
        if self._runner is None:
            self._runner = AgentRunner(
                self.logs, self.config.pass_env_names, self.config.pass_env_prefixes,
                run_observer=self.state.agent_run_observer())
        return self._runner

    @property
    def engine(self) -> WorkflowEngine:
        """Fully wired engine; the only path that builds the kernel stack."""
        if self._engine is None:
            config = self.config
            state = self.state
            run_observer = state.agent_run_observer()
            runner = AgentRunner(self.logs, config.pass_env_names, config.pass_env_prefixes,
                                 run_observer=run_observer,
                                 metadata_collector=GitRunMetadataCollector())
            verifier = Verifier(config.verification_commands,
                                pass_env_names=config.pass_env_names,
                                pass_env_prefixes=config.pass_env_prefixes)
            kernel = VerificationKernel(
                profiles=config.verification_profiles,
                default_profile=config.default_verification_profile,
                legacy_commands=config.verification_commands,
                pass_env_names=config.pass_env_names,
                pass_env_prefixes=config.pass_env_prefixes,
                logs=self.logs,
                state=state,
            )
            self._engine = WorkflowEngine(
                config, state, self.registry, runner, verifier,
                run_observer=run_observer,
                metadata_collector=GitRunMetadataCollector(),
                verification_kernel=kernel)
        return self._engine


def _build_services(args: argparse.Namespace) -> CliServices:
    """Construct the production service bundle for one parsed command line.

    The state root is resolved lazily by :class:`CliServices`, so a failing
    Git lookup still surfaces as the command's own error path.
    """
    return CliServices(config=load_config(args.config), args=args)


def main(argv: list[str] | None = None, services: CliServices | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        try:
            from .gui import main as launch_gui
        except ImportError as error:
            print(f"ERROR: GUI dependencies are unavailable: {error}")
            return 1
        try:
            launch_gui(args.config)
        except Exception as error:
            print(f"ERROR: {error}")
            return 1
        return 0
    if services is None:
        try:
            services = _build_services(args)
        except (OSError, ValueError) as error:
            print(f"ERROR: {error}")
            return 2
    config = services.config
    if args.command == "agents":
        for name, agent in services.registry.detect().items():
            # `ONLINE` previously meant only "the executable is on PATH", which
            # reported agents as selectable that could not run (dead proxy,
            # exhausted quota). Report the health state and the reason instead.
            detail = f"  ({agent.health_detail})" if agent.health_detail else ""
            print(f"{name:<14} {agent.status:<10} {detail}")
        return 0
    # Resolve the canonical location up front: state, logs, and worktree then
    # share one root, and a non-repository `task`/`workflow` directory fails
    # fast with its own message instead of an unrelated downstream error.
    try:
        services.require_state_root()
    except GitError as error:
        print(f"ERROR: {error}")
        return 1
    logs = services.logs
    if args.command == "status":
        state = services.state
        try:
            workflow = state.latest_workflow()
            if workflow is None:
                print("No persisted workflows.")
            else:
                print(f"{workflow.id}  {workflow.status.value}  {workflow.description}")
                for task in state.list_tasks(workflow.id):
                    print(f"  {task.status:<8} {task.role:<16} {task.description}")
                for run in state.list_agent_runs(workflow.id, limit=50):
                    print(
                        f"  run {run.status.value:<10} {run.agent or '<no-agent>':<14} "
                        f"task={run.task_id or '-'} attempt={run.attempt}"
                    )
                for verification in state.list_verification_runs(workflow.id, limit=20):
                    print(
                        f"  verification {verification.overall_status or verification.status.value:<10} "
                        f"{verification.profile_name:<14} task={verification.task_id or '-'}"
                    )
                for failure in state.list_failures(workflow.id, limit=10):
                    print(
                        f"  failure {failure.category.value:<20} {failure.recommended_action.value:<22} "
                        f"task={failure.task_id or '-'} retryable={failure.retryable}"
                    )
                ref = state.get_worktree_ref(workflow.id)
                if ref is not None:
                    print(f"  worktree {ref.branch} base={ref.base_branch}@{ref.base_commit[:12]} path={ref.path}")
        finally:
            services.close()
        return 0
    if args.command == "run":
        registry = services.registry
        # Resolved before the agent lookup, as it was when `run` built the
        # observer inline, so a failed lookup still leaves the same
        # `.agentops/state.sqlite` behind.
        runner = services.runner
        try:
            agent = registry.get(args.agent)
            result = asyncio.run(runner.run_agent(agent, args.prompt, Path.cwd()))
        except (KeyError, RuntimeError, OSError) as error:
            print(f"ERROR: {error}")
            return 1
        finally:
            services.close()
        print(f"[{result.agent}] {'PASSED' if result.succeeded else 'FAILED'} ({result.duration_seconds:.1f}s)")
        if result.run_id is not None:
            print(f"run: {result.run_id}")
        print(f"log: {result.log_path}")
        if result.stdout:
            _print_text(result.stdout.rstrip())
        if result.stderr:
            _print_text(result.stderr.rstrip())
        return 0 if result.succeeded else 1
    if args.command == "logs":
        for path in logs.list_logs(args.task)[: args.tail]:
            print(path)
        return 0
    if args.command == "runs":
        state = services.state
        try:
            try:
                status = AgentRunStatus(args.status) if args.status else None
                runs = state.list_agent_runs(args.workflow, args.task, status, args.limit, args.offset)
            except ValueError as error:
                print(f"ERROR: {error}")
                return 2
            if not runs:
                print("No persisted agent runs.")
            for run in runs:
                duration = f"{run.duration_seconds:.1f}s" if run.duration_seconds is not None else "-"
                print(
                    f"{run.id}  {run.status.value}  {run.agent or '<no-agent>'}  "
                    f"task={run.task_id or '-'} attempt={run.attempt} "
                    f"exit={run.exit_code} duration={duration}"
                )
        finally:
            services.close()
        return 0
    if args.command == "verify":
        state = services.state
        try:
            if args.run:
                run = state.get_verification_run(args.run)
                report = state.get_verification_report_by_run(args.run)
                print(
                    f"{run.id}  {report.overall_status.value}  profile={run.profile_name}  "
                    f"passed={report.passed_checks}/{report.total_checks}  "
                    f"failed={report.failed_checks} skipped={report.skipped_checks}  "
                    f"required_failures={report.required_failures}"
                )
                for check in state.list_verification_checks(args.run):
                    print(
                        f"  {check.status.value:<10} {check.name:<24} "
                        f"{check.check_class.value:<12} exit={check.exit_code}  "
                        f"required={check.required} reason={check.failure_reason or '-'}"
                    )
            else:
                runs = state.list_verification_runs(args.workflow, args.task, limit=args.limit, offset=args.offset)
                if not runs:
                    print("No persisted verification runs.")
                for run in runs:
                    overall = run.overall_status.value if run.overall_status else run.status.value
                    print(
                        f"{run.id}  {overall}  profile={run.profile_name}  "
                        f"task={run.task_id or '-'} passed={run.passed_checks}/{run.total_checks}  "
                        f"required_failures={run.required_failures}"
                    )
        except (KeyError, ValueError) as error:
            print(f"ERROR: {error}")
            return 2
        finally:
            services.close()
        return 0
    if args.command == "events":
        state = services.state
        try:
            found = state.query_events(
                args.workflow, args.task, args.run, args.type, args.limit, args.offset
            )
            if not found:
                print("No timeline events.")
            for event in found:
                print(
                    f"{event.timestamp}  {event.type.value:<24} {event.severity.value:<8} "
                    f"workflow={event.workflow_id or '-'} task={event.task_id or '-'} "
                    f"run={event.agent_run_id or '-'} {event.message or ''}"
                )
        finally:
            services.close()
        return 0
    if args.command == "artifacts":
        state = services.state
        try:
            store = services.artifact_store
            if args.show:
                artifact = state.get_artifact(args.show)
                if artifact is None:
                    print(f"ERROR: unknown artifact {args.show}")
                    return 2
                try:
                    _print_text(store.read_text(artifact))
                except (ArtifactError, OSError) as error:
                    print(f"ERROR: {error}")
                    return 2
                return 0
            found = state.list_artifacts(
                args.workflow, args.task, args.kind, args.limit, args.offset
            )
            if args.prune_keep is not None:
                deleted = store.prune(found, keep_last_n=args.prune_keep)
                for artifact_id in deleted:
                    state.delete_artifact_record(artifact_id)
                print(f"Pruned {len(deleted)} artifact(s).")
                return 0
            if not found:
                print("No stored artifacts.")
            for artifact in found:
                print(
                    f"{artifact.id}  {artifact.kind.value:<20} {artifact.name:<28} "
                    f"{artifact.size_bytes}B sha256={artifact.sha256[:12]} "
                    f"workflow={artifact.workflow_id or '-'}"
                )
        finally:
            services.close()
        return 0
    if args.command == "failures":
        state = services.state
        try:
            try:
                failures = state.list_failures(
                    args.workflow, args.task, args.category, args.limit, args.offset
                )
            except ValueError as error:
                print(f"ERROR: {error}")
                return 2
            if not failures:
                print("No persisted failures.")
            for failure in failures:
                print(
                    f"{failure.id}  {failure.category.value}  {failure.severity.value}  "
                    f"action={failure.recommended_action.value}  task={failure.task_id or '-'}  "
                    f"retryable={failure.retryable} repairable={failure.repairable}  "
                    f"recovery={failure.recovery_state.value if failure.recovery_state else '-'}"
                )
        finally:
            services.close()
        return 0
    if args.command == "recover":
        state = services.state
        try:
            summary = state.recover_all()
            print(
                f"Recovered agent_runs={summary['agent_runs']} "
                f"verification_runs={summary['verification_runs']} tasks={summary['tasks']}. "
                "Interrupted work is marked failed (never success without evidence); "
                "worktrees preserved."
            )
        finally:
            services.close()
        return 0
    try:
        definition = None if args.command == "task" else _load_data(args.file)
        description = args.description if args.command == "task" else definition.get("description")
    except (OSError, ValueError, AttributeError) as error:
        print(f"ERROR: invalid workflow file: {error}")
        return 2
    if not isinstance(description, str) or not description.strip():
        print("ERROR: workflow file requires a string 'description'.")
        return 2
    state = services.state
    engine = services.engine
    manager = services.manager
    worktree = None
    workflow_id = None
    remove_worktree = False
    try:
        worktree = manager.create(args.cwd, description)
        if definition and definition.get("tasks"):
            specifications = definition["tasks"]
            if not isinstance(specifications, list) or not all(isinstance(item, dict) for item in specifications):
                print("ERROR: workflow 'tasks' must be a list of mappings.")
                return 2
            workflow_id, _ = engine.create_workflow(description, specifications)
            asyncio.run(engine.execute(workflow_id, worktree.path))
            # Custom DAGs get the same READY contract as the standard flow:
            # passed+verified verification, a passed review, and evidence.
            readiness = engine.workflow_readiness(workflow_id)
            result = WorkflowResult(workflow_id, readiness.ready, readiness.summary())
        else:
            result = asyncio.run(engine.run_high_level(description, worktree.path))
        workflow_id = result.workflow_id
        # Phase 3: persist provenance so retry/merge validate against the
        # stored base even after restart (never memory-only).
        record_worktree_provenance(state, worktree, workflow_id, DegradationRecorder(
            emit=event_emitter(state.record_typed_event)))
        finalization = WorktreeFinalization(changed=False, merged=False, conflict_error=None)
        # Finalize whenever the IMPLEMENTATION succeeded, even if verification did
        # not reach PASSED. Previously only `result.ready` finalized, so a workflow
        # whose required verification check was inapplicable (report UNVERIFIED,
        # task BLOCKED) printed "no worktree changes to merge" and threw away
        # correct work: a delegate wrote PROOF.md, and the file was left untracked
        # in a worktree that got preserved "for inspection". Refusing to merge
        # work nobody proved is wrong is the opposite mistake, so readiness is
        # deliberately NOT the gate here -- a substantive verification FAILURE
        # still keeps the work unmerged, because finalization is only attempted
        # when the implementation task itself passed.
        implementation_ok = _implementation_passed(state, workflow_id)
        if result.ready or implementation_ok:
            finalization = finalize_worktree(manager, state, worktree, description, workflow_id,
                                             config.max_attempts)
            if finalization.conflict_error is not None:
                print(f"CONFLICT: {finalization.conflict_error}")
                print("A persisted conflict-resolution task was created; the worktree is preserved.")
                return 1
        changed = finalization.changed
        remove_worktree = result.ready
        print(f"RESULT: {result.summary}")
        print(f"workflow: {result.workflow_id}")
        print("merged worktree changes" if changed else "no worktree changes to merge")
        return 0 if result.ready else 1
    except Exception as error:
        print(f"ERROR: {error}")
        return 1
    finally:
        if worktree is not None and remove_worktree:
            try:
                manager.remove(worktree)
            except GitError as error:
                print(f"Worktree preserved at {worktree.path}: {error}")
        elif worktree is not None:
            print(f"Worktree preserved at {worktree.path} for inspection or repair.")
        services.close()
