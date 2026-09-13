"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import __version__
from .config import load_config
from .config import _load_data
from .git import GitError, GitWorktreeManager
from .registry import AgentRegistry
from .logging import LogManager
from .runner import AgentRunner
from .state import StateStore
from .tasks import Task
from .verification import Verifier
from .workflow import WorkflowEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentops", description="Local-first coding-agent orchestration")
    parser.add_argument("--config", type=Path, help="Path to YAML configuration")
    parser.add_argument("--version", action="version", version=f"agentops {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("agents", help="Detect configured coding-agent CLIs")
    subcommands.add_parser("status", help="Show persistent workflow status")
    run = subcommands.add_parser("run", help="Run one installed agent")
    run.add_argument("agent")
    run.add_argument("prompt")
    task = subcommands.add_parser("task", help="Execute a high-level task workflow")
    task.add_argument("description")
    task.add_argument("--cwd", type=Path, default=Path.cwd())
    logs = subcommands.add_parser("logs", help="Inspect saved task and runner logs")
    logs.add_argument("--task")
    logs.add_argument("--tail", type=int, default=30)
    workflow = subcommands.add_parser("workflow", help="Execute a YAML workflow file")
    workflow.add_argument("file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "agents":
        for name, agent in AgentRegistry(config).detect().items():
            print(f"{name:<14} {'ONLINE' if agent.available else 'OFFLINE'}")
        return 0
    state_root = (getattr(args, "cwd", None) or Path.cwd()) / ".agentops"
    logs = LogManager(state_root / "logs")
    if args.command == "status":
        state = StateStore(state_root / "state.sqlite")
        try:
            workflow = state.latest_workflow()
            if workflow is None:
                print("No persisted workflows.")
            else:
                print(f"{workflow['id']}  {workflow['status']}  {workflow['description']}")
                for task in state.list_tasks(workflow["id"]):
                    print(f"  {task.status:<8} {task.role:<16} {task.description}")
        finally:
            state.close()
        return 0
    if args.command == "run":
        registry = AgentRegistry(config)
        try:
            agent = registry.get(args.agent)
            result = asyncio.run(AgentRunner(logs).run_agent(agent, args.prompt, Path.cwd()))
        except (KeyError, RuntimeError, OSError) as error:
            print(f"ERROR: {error}")
            return 1
        print(f"[{result.agent}] {'PASSED' if result.succeeded else 'FAILED'} ({result.duration_seconds:.1f}s)")
        print(f"log: {result.log_path}")
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip())
        return 0 if result.succeeded else 1
    if args.command == "logs":
        for path in logs.list_logs(args.task)[: args.tail]:
            print(path)
        return 0
    definition = None if args.command == "task" else _load_data(args.file)
    description = args.description if args.command == "task" else definition.get("description")
    if not isinstance(description, str) or not description.strip():
        print("ERROR: workflow file requires a string 'description'.")
        return 2
    state = StateStore(state_root / "state.sqlite")
    registry = AgentRegistry(config)
    engine = WorkflowEngine(config, state, registry, AgentRunner(logs), Verifier(config.verification_commands))
    manager = GitWorktreeManager()
    worktree = None
    workflow_id = None
    remove_worktree = False
    try:
        worktree = manager.create(args.cwd if args.command == "task" else Path.cwd(), description)
        if definition and definition.get("tasks"):
            specifications = definition["tasks"]
            if not isinstance(specifications, list) or not all(isinstance(item, dict) for item in specifications):
                print("ERROR: workflow 'tasks' must be a list of mappings.")
                return 2
            workflow_id, _ = engine.create_workflow(description, specifications)
            asyncio.run(engine.execute(workflow_id, worktree.path))
            status = state.refresh_workflow_status(workflow_id)
            from .workflow import WorkflowResult
            result = WorkflowResult(workflow_id, status.value == "passed", "READY" if status.value == "passed" else str(status))
        else:
            result = asyncio.run(engine.run_high_level(description, worktree.path))
        workflow_id = result.workflow_id
        changed = False
        if result.ready:
            changed = manager.commit_changes(worktree, f"agentops: {description}")
            if changed:
                try:
                    manager.merge(worktree)
                except GitError as error:
                    state.add_task(Task(
                        f"Resolve Git merge conflict for '{description}'.\n{error}", "debugging", workflow_id,
                        max_attempts=config.max_attempts,
                    ))
                    print(f"CONFLICT: {error}")
                    print("A persisted conflict-resolution task was created; the worktree is preserved.")
                    return 1
        remove_worktree = result.ready
        print(f"RESULT: {result.summary}")
        print(f"workflow: {result.workflow_id}")
        print("merged worktree changes" if changed else "no worktree changes to merge")
        return 0 if result.ready else 1
    except (GitError, OSError, RuntimeError) as error:
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
