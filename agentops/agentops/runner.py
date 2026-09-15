"""Safe local process runner for configured coding-agent CLIs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .agent_run import (
    AgentRunContext,
    AgentRunMetadata,
    AgentRunObserver,
    AgentRunOutcome,
    AgentRunStatus,
)
from .agent_result import parse_agent_result
from .config import AgentConfig
from .logging import LogManager, RunLogArtifacts
from .registry import DetectedAgent


class OperationCancelled(RuntimeError):
    """Raised when a running agent observes a cancellation request."""


def _safe_structured_result(stdout: object) -> object | None:
    """Normalize agent stdout without ever breaking a valid execution.

    Parse warnings and the parse mode are folded into the stored envelope
    so evidence (e.g. unsupported schema_version) survives persistence.
    """
    try:
        parsed = parse_agent_result(stdout)
    except Exception:
        return None
    try:
        result = parsed.result
        extra_warnings = tuple(dict.fromkeys((*result.warnings, *parsed.warnings)))
        metadata = dict(result.metadata)
        metadata.setdefault("parse_mode", parsed.parse_mode.value)
        from dataclasses import replace
        result = replace(result, warnings=extra_warnings, metadata=metadata)
        return result.to_dict()
    except Exception:
        return None


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
    run_id: str | None = None
    cancelled: bool = False
    terminated: bool = False
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    structured_result: object | None = None

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class AgentRunner:
    def __init__(
        self,
        logs: LogManager,
        pass_env_names: tuple[str, ...] = (),
        pass_env_prefixes: tuple[str, ...] = (),
        run_observer: AgentRunObserver | None = None,
        metadata_collector: Callable[[str | Path], AgentRunMetadata] | None = None,
    ):
        self.logs = logs
        self.pass_env_names = pass_env_names
        self.pass_env_prefixes = pass_env_prefixes
        self.run_observer = run_observer
        self.metadata_collector = metadata_collector

    @staticmethod
    def _environment(pass_env_names: tuple[str, ...] = (), pass_env_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
        allowed = {
            "PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "LANG", "LC_ALL",
        }
        # Windows environment variables are case-insensitive, but iterating
        # os.environ yields the parent's raw casing (e.g. SYSTEMROOT, PATH).
        # A case-sensitive allowlist check would then silently drop them and
        # launch agents without PATH or %SystemRoot% (fatal to Bun-based CLIs).
        fold = (lambda name: name.upper()) if os.name == "nt" else (lambda name: name)
        allowed_folded = {fold(name) for name in allowed}
        canonical = {}
        for name in allowed:
            canonical.setdefault(fold(name), name)
        pass_names_folded = {fold(name) for name in pass_env_names}
        pass_prefixes_folded = tuple(fold(prefix) for prefix in pass_env_prefixes)
        env: dict[str, str] = {}
        for name, value in os.environ.items():
            folded = fold(name)
            if folded in allowed_folded:
                env.setdefault(canonical[folded], value)
            elif folded in pass_names_folded or any(folded.startswith(prefix) for prefix in pass_prefixes_folded):
                env.setdefault(name, value)
        if os.name == "nt":
            # Bun-based agent CLIs (e.g. claude) require %SystemRoot% for
            # network requests and die instantly without it. The parent
            # process normally provides it, but when AgentOps itself is
            # launched from a scrubbed environment (sandbox, clean-env tool),
            # fall back to the standard location instead of failing cryptically.
            system_root = env.get("SystemRoot") or r"C:\WINDOWS"
            env.setdefault("SystemRoot", system_root)
            env.setdefault("WINDIR", env.get("WINDIR") or system_root)
        return env

    @staticmethod
    async def terminate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        """Terminate a timed out process group and bound cleanup waits."""
        if process.returncode is None:
            try:
                if sys.platform == "win32" and isinstance(process.pid, int):
                    cleanup = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(cleanup.wait(), timeout=10)
                elif sys.platform != "win32" and isinstance(process.pid, int):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, OSError, TimeoutError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        try:
            return await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            return b"", b"Process did not exit within the cleanup timeout."

    @staticmethod
    def build_command(agent: DetectedAgent | AgentConfig, prompt: str) -> tuple[str, ...]:
        if isinstance(agent, DetectedAgent):
            config = agent.config
            # Use the absolute path resolved at detection time instead of the
            # bare command name, so execution cannot pick up a different binary
            # if PATH changes between detection and run (TOCTOU). Users who
            # need a pinned binary can set an absolute path as `command` in
            # agents.yaml; shutil.which passes absolute paths through.
            executable = agent.executable or config.command
        else:
            config = agent
            executable = config.command
        return (executable, *(argument.replace("{prompt}", prompt) for argument in config.args))

    @staticmethod
    async def _communicate_with_cancel(
        process: asyncio.subprocess.Process,
        cancel_event: threading.Event | None,
    ) -> tuple[bytes, bytes]:
        """Wait for process output while allowing a thread-owned cancel event."""
        if cancel_event is None:
            stdout, stderr = await process.communicate()
            return stdout, stderr
        if cancel_event.is_set():
            raise OperationCancelled
        communicate = asyncio.create_task(process.communicate())
        wait_for_cancel = asyncio.create_task(asyncio.to_thread(cancel_event.wait))
        try:
            done, _ = await asyncio.wait({communicate, wait_for_cancel}, return_when=asyncio.FIRST_COMPLETED)
            if communicate in done:
                stdout, stderr = communicate.result()
                return stdout, stderr
            communicate.cancel()
            with suppress(asyncio.CancelledError):
                await communicate
            raise OperationCancelled
        finally:
            for pending_task in (communicate, wait_for_cancel):
                if not pending_task.done():
                    pending_task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(communicate, wait_for_cancel)

    @staticmethod
    def _notify_create(observer: AgentRunObserver | None, context: AgentRunContext) -> str | None:
        if observer is None:
            return None
        try:
            created = observer.create_run(context)
            return created.id if hasattr(created, "id") else str(created)
        except Exception:
            # Persistence must not prevent a configured local agent from
            # running.  The caller can still report the process outcome.
            return None

    @staticmethod
    def _notify_starting(observer: AgentRunObserver | None, run_id: str | None) -> None:
        if observer is not None and run_id is not None:
            try:
                observer.mark_starting(run_id)
            except Exception:
                pass

    @staticmethod
    def _notify_running(observer: AgentRunObserver | None, run_id: str | None) -> None:
        if observer is not None and run_id is not None:
            try:
                observer.mark_running(run_id)
            except Exception:
                pass

    @staticmethod
    def _notify_finish(observer: AgentRunObserver | None, run_id: str | None, outcome: AgentRunOutcome) -> None:
        if observer is not None and run_id is not None:
            try:
                observer.finish_run(run_id, outcome)
            except Exception:
                pass

    @staticmethod
    def _notify_failure(
        observer: AgentRunObserver | None,
        run_id: str | None,
        error: BaseException,
        classification: str,
    ) -> None:
        if observer is not None and run_id is not None:
            try:
                observer.fail_run(run_id, error, classification)
            except Exception:
                pass

    async def run_agent(
        self,
        agent: DetectedAgent,
        prompt: str,
        working_directory: str | Path,
        task_id: str | None = None,
        timeout_seconds: int | None = None,
        cancel_event: threading.Event | None = None,
        run_context: AgentRunContext | None = None,
        run_observer: AgentRunObserver | None = None,
        metadata_collector: Callable[[str | Path], AgentRunMetadata] | None = None,
    ) -> RunResult:
        if not agent.available:
            raise RuntimeError(f"Agent '{agent.config.name}' is unavailable.")
        command = self.build_command(agent, prompt)
        timeout = timeout_seconds or agent.config.timeout_seconds
        context = run_context or AgentRunContext(
            agent=agent.config.name,
            executable=agent.executable or agent.config.command,
            model=agent.config.model,
            task_id=task_id,
            working_directory=str(Path(working_directory).resolve()),
            prompt=prompt,
            command=command,
        )
        observer = run_observer or self.run_observer
        run_id = self._notify_create(observer, context)
        self._notify_starting(observer, run_id)
        started = monotonic()
        process: asyncio.subprocess.Process | None = None
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(working_directory),
                env=self._environment(self.pass_env_names, self.pass_env_prefixes),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}),
            )
            self._notify_running(observer, run_id)
            timed_out = False
            cancelled = False
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    self._communicate_with_cancel(process, cancel_event),
                    timeout=timeout,
                )
            except TimeoutError:
                timed_out = True
                stdout_bytes, stderr_bytes = await self.terminate(process)
            except OperationCancelled:
                cancelled = True
                stdout_bytes, stderr_bytes = await self.terminate(process)
                raise
            duration = monotonic() - started
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = None if timed_out else process.returncode
            terminated = not timed_out and not cancelled and exit_code is not None and exit_code < 0
            collector = metadata_collector or self.metadata_collector
            metadata = AgentRunMetadata()
            if collector is not None:
                try:
                    metadata = collector(working_directory)
                except Exception:
                    metadata = AgentRunMetadata()
            metadata_text = (
                f"agent={agent.config.name}\n"
                f"command_metadata={json.dumps(context.command_metadata, sort_keys=True)}\n"
                f"exit_code={exit_code}\n"
                f"timed_out={timed_out}\n"
                f"cancelled={cancelled}\n"
                f"terminated={terminated}\n"
                f"duration_seconds={duration:.3f}\n"
            )
            log_task_id = context.task_id or task_id or str(uuid4())
            writer = getattr(self.logs, "write_run_artifacts", None)
            artifacts: RunLogArtifacts | None = None
            if callable(writer):
                candidate = writer(log_task_id, agent.config.name, stdout, stderr, metadata_text)
                if isinstance(candidate, RunLogArtifacts):
                    artifacts = candidate
            if artifacts is None:
                # Preserve compatibility with LogManager implementations that
                # only expose the original write_run() entry point.
                metadata_path = self.logs.write_run(log_task_id, agent.config.name, stdout, stderr, metadata_text)
                artifacts = RunLogArtifacts(None, None, metadata_path)
            stdout_path = str(artifacts.stdout_path) if artifacts.stdout_path is not None else None
            stderr_path = str(artifacts.stderr_path) if artifacts.stderr_path is not None else None
            log_path = str(artifacts.metadata_path) if artifacts.metadata_path is not None else None
            structured_result = _safe_structured_result(stdout)
            status = (
                AgentRunStatus.TIMED_OUT if timed_out else
                AgentRunStatus.CANCELLED if cancelled else
                AgentRunStatus.TERMINATED if terminated else
                AgentRunStatus.COMPLETED if exit_code == 0 else
                AgentRunStatus.FAILED
            )
            outcome = AgentRunOutcome(
                status=status,
                exit_code=exit_code,
                duration_seconds=duration,
                cancelled=cancelled,
                timed_out=timed_out,
                terminated=terminated,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                log_path=log_path,
                files_changed=metadata.files_changed,
                diff_stat=metadata.diff_stat,
                failure_classification=(
                    None if status is AgentRunStatus.COMPLETED else
                    "timeout" if status is AgentRunStatus.TIMED_OUT else
                    "cancelled" if status is AgentRunStatus.CANCELLED else
                    "terminated" if status is AgentRunStatus.TERMINATED else
                    "nonzero_exit"
                ),
                structured_result=structured_result,
            )
            self._notify_finish(observer, run_id, outcome)
            result = RunResult(
                agent.config.name,
                command,
                exit_code,
                stdout,
                stderr,
                duration,
                timed_out,
                artifacts.metadata_path,  # type: ignore[arg-type]
                run_id=run_id,
                cancelled=cancelled,
                terminated=terminated,
                stdout_path=artifacts.stdout_path,
                stderr_path=artifacts.stderr_path,
                structured_result=structured_result,
            )
            return result
        except OperationCancelled:
            duration = monotonic() - started
            outcome = AgentRunOutcome(
                status=AgentRunStatus.CANCELLED,
                duration_seconds=duration,
                cancelled=True,
                failure_classification="cancelled",
            )
            self._notify_finish(observer, run_id, outcome)
            raise
        except TimeoutError:
            duration = monotonic() - started
            outcome = AgentRunOutcome(
                status=AgentRunStatus.TIMED_OUT,
                duration_seconds=duration,
                timed_out=True,
                failure_classification="timeout",
            )
            self._notify_finish(observer, run_id, outcome)
            raise
        except Exception as error:
            duration = monotonic() - started
            outcome = AgentRunOutcome(
                status=AgentRunStatus.FAILED,
                duration_seconds=duration,
                error=str(error),
                failure_classification="process_error",
            )
            self._notify_finish(observer, run_id, outcome)
            raise
