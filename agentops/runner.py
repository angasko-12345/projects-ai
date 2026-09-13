"""Safe local process runner for configured coding-agent CLIs."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .config import AgentConfig
from .logging import LogManager
from .registry import DetectedAgent


@dataclass(frozen=True)
class RunResult:
    agent: str
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    log_path: Path

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class AgentRunner:
    def __init__(self, logs: LogManager):
        self.logs = logs

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = {
            "PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "LANG", "LC_ALL",
        }
        return {name: value for name, value in os.environ.items() if name in allowed}

    @staticmethod
    def build_command(agent: DetectedAgent | AgentConfig, prompt: str) -> tuple[str, ...]:
        config = agent.config if isinstance(agent, DetectedAgent) else agent
        return (config.command, *(argument.replace("{prompt}", prompt) for argument in config.args))

    async def run_agent(
        self,
        agent: DetectedAgent,
        prompt: str,
        working_directory: str | Path,
        task_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RunResult:
        if not agent.available:
            raise RuntimeError(f"Agent '{agent.config.name}' is unavailable.")
        command = self.build_command(agent, prompt)
        timeout = timeout_seconds or agent.config.timeout_seconds
        started = monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(working_directory),
            env=self._environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        duration = monotonic() - started
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = None if timed_out else process.returncode
        metadata = (
            f"agent={agent.config.name}\ncommand={command!r}\nexit_code={exit_code}\n"
            f"timed_out={timed_out}\nduration_seconds={duration:.3f}\n"
        )
        log_path = self.logs.write_run(task_id or str(uuid4()), agent.config.name, stdout, stderr, metadata)
        return RunResult(agent.config.name, command, exit_code, stdout, stderr, duration, timed_out, log_path)
