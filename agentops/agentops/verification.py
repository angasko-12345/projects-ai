"""Allowlisted project verification commands."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .runtime import OperationCancelled, ProcessRuntime


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
                 pass_env_names: tuple[str, ...] = (), pass_env_prefixes: tuple[str, ...] = (),
                 runtime: ProcessRuntime | None = None):
        self.commands = commands
        self.timeout_seconds = timeout_seconds
        self.pass_env_names = pass_env_names
        self.pass_env_prefixes = pass_env_prefixes
        self._runtime = runtime or ProcessRuntime(pass_env_names, pass_env_prefixes)

    async def run(
        self,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> list[CheckResult]:
        results: list[CheckResult] = []
        for command in self.commands:
            started = monotonic()
            completed = await self._runtime.run_process(
                command, cwd=working_directory,
                env=self._runtime.environment(),
                timeout=self.timeout_seconds,
                cancel_event=cancel_event,
                stderr=asyncio.subprocess.STDOUT,
            )
            if completed.cancelled:
                raise OperationCancelled
            output = completed.stdout
            timed_out = completed.timed_out
            result = CheckResult(command, completed.exit_code,
                                 output.decode("utf-8", errors="replace"), timed_out, monotonic() - started)
            results.append(result)
            if not result.succeeded:
                break
        return results
