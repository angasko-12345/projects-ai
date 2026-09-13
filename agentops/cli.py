"""Command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .config import load_config
from .registry import AgentRegistry


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
    if args.command == "status":
        print("No workflow state has been requested yet.")
        return 0
    print(f"The '{args.command}' command is available after Stage 2 initialization.")
    return 0
