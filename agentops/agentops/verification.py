"""Allowlisted project verification commands."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .runner import AgentRunner, OperationCancelled


@dataclass(frozen=True)
class CheckResult:
    command: tuple[str, ...]
    exit_code: int | None
    output: str
    timed_out: bool
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class Verifier:
    def __init__(self, commands: tuple[tuple[str, ...], ...], timeout_seconds: int = 300,
                 pass_env_names: tuple[str, ...] = (), pass_env_prefixes: tuple[str, ...] = ()):
        self.commands = commands
        self.timeout_seconds = timeout_seconds
        self.pass_env_names = pass_env_names
        self.pass_env_prefixes = pass_env_prefixes

    async def run(
        self,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        for command in self.commands:
            started = monotonic()
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(working_directory),
                env=AgentRunner._environment(self.pass_env_names, self.pass_env_prefixes),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            timed_out = False
            try:
                output, _ = await asyncio.wait_for(
                    AgentRunner._communicate_with_cancel(process, cancel_event),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                timed_out = True
                output, _ = await AgentRunner.terminate(process)
            except OperationCancelled:
                output, _ = await AgentRunner.terminate(process)
                raise
            result = CheckResult(command, None if timed_out else process.returncode,
                                 output.decode("utf-8", errors="replace"), timed_out, monotonic() - started)
            results.append(result)
            if not result.succeeded:
                break
        return results
