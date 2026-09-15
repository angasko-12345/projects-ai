"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .agent_run import AgentRunStatus, GitRunMetadataCollector
from .artifacts import ArtifactError
from .config import _load_data, load_config
from .finalize import WorktreeFinalization, finalize_worktree
from .git import GitError, GitWorktreeManager, WorktreeRef
from .registry import AgentRegistry
from .logging import LogManager
from .runner import AgentRunner
from .state import StateStore
from .verification import Verifier
from .verification_kernel import VerificationKernel
from .workflow import WorkflowEngine, WorkflowResult


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


def main(argv: list[str] | None = None) -> int:
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
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}")
        return 2
    if args.command == "agents":
        for name, agent in AgentRegistry(config).detect().items():
            status = "ONLINE" if agent.available else ("DISABLED" if not agent.config.enabled else "OFFLINE")
            print(f"{name:<14} {status}")
        return 0
    command_base = getattr(args, "cwd", None) or Path.cwd()
    if args.command in ("task", "workflow"):
        # Worktrees require a Git repository, so resolve the canonical root
        # up front: state, logs, and worktree then share one location, and a
        # non-repository directory fails fast without stray .agentops files.
        try:
            state_root = GitWorktreeManager().repository_root(command_base) / ".agentops"
        except GitError as error:
            print(f"ERROR: {error}")
            return 1
    else:
        state_root = _resolve_state_base(command_base) / ".agentops"
    logs = LogManager(state_root / "logs")
    if args.command == "status":
        state = StateStore(state_root / "state.sqlite")
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
            state.close()
        return 0
    if args.command == "run":
        registry = AgentRegistry(config)
        state = StateStore(state_root / "state.sqlite")
        try:
            agent = registry.get(args.agent)
            observer = state.agent_run_observer()
            runner = AgentRunner(logs, config.pass_env_names, config.pass_env_prefixes,
                                 run_observer=observer)
            result = asyncio.run(runner.run_agent(agent, args.prompt, Path.cwd()))
        except (KeyError, RuntimeError, OSError) as error:
            print(f"ERROR: {error}")
            return 1
        finally:
            state.close()
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
        state = StateStore(state_root / "state.sqlite")
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
            state.close()
        return 0
    if args.command == "verify":
        state = StateStore(state_root / "state.sqlite")
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
            state.close()
        return 0
    if args.command == "events":
        state = StateStore(state_root / "state.sqlite")
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
            state.close()
        return 0
    if args.command == "artifacts":
        from .artifacts import ArtifactStore

        state = StateStore(state_root / "state.sqlite")
        try:
            store = ArtifactStore(state_root / "artifacts")
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
            state.close()
        return 0
    if args.command == "failures":
        state = StateStore(state_root / "state.sqlite")
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
            state.close()
        return 0
    if args.command == "recover":
        state = StateStore(state_root / "state.sqlite")
        try:
            summary = state.recover_all()
            print(
                f"Recovered agent_runs={summary['agent_runs']} "
                f"verification_runs={summary['verification_runs']} tasks={summary['tasks']}. "
                "Interrupted work is marked failed (never success without evidence); "
                "worktrees preserved."
            )
        finally:
            state.close()
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
    state = StateStore(state_root / "state.sqlite")
    registry = AgentRegistry(config)
    run_observer = state.agent_run_observer()
    runner = AgentRunner(logs, config.pass_env_names, config.pass_env_prefixes,
                         run_observer=run_observer, metadata_collector=GitRunMetadataCollector())
    verifier = Verifier(config.verification_commands, pass_env_names=config.pass_env_names,
                        pass_env_prefixes=config.pass_env_prefixes)
    verification_kernel = VerificationKernel(
        profiles=config.verification_profiles,
        default_profile=config.default_verification_profile,
        legacy_commands=config.verification_commands,
        pass_env_names=config.pass_env_names,
        pass_env_prefixes=config.pass_env_prefixes,
        logs=logs,
        state=state,
    )
    engine = WorkflowEngine(config, state, registry, runner, verifier,
                            run_observer=run_observer, metadata_collector=GitRunMetadataCollector(),
                            verification_kernel=verification_kernel)
    manager = GitWorktreeManager()
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
            status = state.refresh_workflow_status(workflow_id)
            evidence = engine.verification_evidence(workflow_id)
            ready = status.value == "passed" and bool(evidence)
            summary = (
                "READY" if ready else
                "No passed verification evidence; workflow is not READY." if status.value == "passed"
                else str(status)
            )
            result = WorkflowResult(workflow_id, ready, summary)
        else:
            result = asyncio.run(engine.run_high_level(description, worktree.path))
        workflow_id = result.workflow_id
        # Phase 3: persist provenance so retry/merge validate against the
        # stored base even after restart (never memory-only).
        try:
            from uuid import uuid4 as _uuid4
            from .tasks import utc_now as _utc_now
            if worktree is not None and workflow_id is not None:
                state.record_worktree_ref(WorktreeRef(
                    id=str(_uuid4()), workflow_id=workflow_id, path=str(worktree.path),
                    branch=worktree.branch, base_branch=worktree.base_branch,
                    base_commit=worktree.base_commit, created_at=_utc_now(),
                ))
        except Exception:
            pass
        finalization = WorktreeFinalization(changed=False, merged=False, conflict_error=None)
        if result.ready:
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
        state.close()
