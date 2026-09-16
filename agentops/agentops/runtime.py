"""Shared local process runtime for agents and verification.

This module owns the process behaviors previously duplicated across
:class:`agentops.runner.AgentRunner`,
:class:`agentops.verification_kernel.VerificationKernel`, and
:class:`agentops.verification.Verifier`: reduced-environment construction,
unified spawn policy, cancellation-aware communication, timeouts,
process-group termination, and byte-level result capture.

The runtime never interprets command output, writes logs, touches SQLite, or
imports service modules.  Callers retain responsibility for decoding output,
classifying results, persisting runs, and emitting domain events.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any


class OperationCancelled(RuntimeError):
    """Raised when a running process observes a cancellation request."""


@dataclass(frozen=True)
class ProcessResult:
    """Byte-level outcome for one runtime-managed process."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False


SpawnFactory = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ProcessRuntime:
    """One injectable policy object for local subprocess execution."""

    pass_env_names: tuple[str, ...] = ()
    pass_env_prefixes: tuple[str, ...] = ()
    spawn: SpawnFactory | None = None
    cleanup_timeout_seconds: float = 10.0

    @staticmethod
    def build_environment(
        pass_env_names: tuple[str, ...] = (),
        pass_env_prefixes: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """Build the reduced environment inherited by every child process."""
        allowed = {
            "PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "LANG", "LC_ALL",
        }
        # Windows environment variables are case-insensitive, but iterating
        # os.environ yields the parent's raw casing (e.g. SYSTEMROOT, PATH).
        # A case-sensitive allowlist check would then silently drop them and
        # launch processes without PATH or %SystemRoot%.
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
            # Bun-based CLIs (e.g. claude) require %SystemRoot% for network
            # requests.  Fall back to the standard location when AgentOps is
            # launched from a scrubbed parent environment.
            system_root = env.get("SystemRoot") or r"C:\WINDOWS"
            env.setdefault("SystemRoot", system_root)
            env.setdefault("WINDIR", env.get("WINDIR") or system_root)
        return env

    def environment(self) -> dict[str, str]:
        """Build the runtime-configured child environment."""
        return self.build_environment(self.pass_env_names, self.pass_env_prefixes)

    @staticmethod
    def spawn_options(platform: str | None = None) -> dict[str, object]:
        """Return the unified default spawn policy for one platform."""
        current = platform or sys.platform
        if current == "win32":
            # CREATE_NO_WINDOW keeps windowed GUI builds from flashing a
            # console per child.  CREATE_NEW_PROCESS_GROUP preserves the
            # existing Windows tree-cleanup semantics.
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
            return {"creationflags": flags}
        # A new POSIX session/process group lets terminate_process() use
        # killpg() against exactly this child tree, never the parent group.
        return {"start_new_session": True}

    async def spawn_process(
        self,
        *command: str,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        stdout: object = asyncio.subprocess.PIPE,
        stderr: object = asyncio.subprocess.PIPE,
    ) -> Any:
        """Spawn a child with the runtime policy unless overridden."""
        factory = self.spawn or asyncio.create_subprocess_exec
        options = self.spawn_options() if self.spawn is None else {}
        return await factory(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            env=self.environment() if env is None else env,
            stdout=stdout,
            stderr=stderr,
            **options,
        )

    #: Maximum delay between cooperative-cancellation checks.  Cancellation
    #: is polled (rather than awaited on a thread) so a normal completion
    #: never leaves a worker thread blocked on an unset event.
    CANCEL_POLL_SECONDS = 0.05

    @staticmethod
    async def communicate_with_cancel(
        process: Any,
        cancel_event: threading.Event | None,
    ) -> tuple[bytes, bytes]:
        """Wait for process output while allowing a thread-owned cancel event."""
        if cancel_event is None:
            stdout, stderr = await process.communicate()
            return stdout, stderr
        if cancel_event.is_set():
            raise OperationCancelled
        communicate = asyncio.create_task(process.communicate())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {communicate}, timeout=ProcessRuntime.CANCEL_POLL_SECONDS,
                )
                if communicate in done:
                    stdout, stderr = communicate.result()
                    return stdout, stderr
                if cancel_event.is_set():
                    communicate.cancel()
                    with suppress(asyncio.CancelledError):
                        await communicate
                    raise OperationCancelled
        finally:
            if not communicate.done():
                communicate.cancel()
            with suppress(asyncio.CancelledError):
                await communicate

    async def terminate_process(
        self,
        process: Any,
        cleanup_timeout: float | None = None,
        *,
        process_group: bool = True,
        platform: str | None = None,
    ) -> tuple[bytes, bytes]:
        """Terminate a process tree and bound the final cleanup wait.

        ``process_group`` records whether the process was spawned in its own
        process group by this runtime.  Custom factories bypass the unified
        spawn policy, so their children must be killed directly: ``killpg``
        against an unknown PID could target the parent's process group.
        """
        timeout = self.cleanup_timeout_seconds if cleanup_timeout is None else cleanup_timeout
        current = platform or sys.platform
        if getattr(process, "returncode", None) is None:
            try:
                pid = getattr(process, "pid", None)
                if current == "win32" and isinstance(pid, int):
                    cleanup = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        **self.spawn_options(platform=current),
                    )
                    await asyncio.wait_for(cleanup.wait(), timeout=timeout)
                elif current != "win32" and isinstance(pid, int) and process_group:
                    os.killpg(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                else:
                    process.kill()
            except (ProcessLookupError, OSError, TimeoutError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        try:
            return await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            return b"", b"Process did not exit within the cleanup timeout."

    async def run_process(
        self,
        command: Iterable[str],
        *,
        cwd: str | Path | None,
        env: dict[str, str] | None = None,
        timeout: float,
        cancel_event: threading.Event | None = None,
        stderr: object = asyncio.subprocess.PIPE,
        on_running: Callable[[Any], None] | None = None,
    ) -> ProcessResult:
        """Run one command with timeout, cooperative cancellation, and cleanup."""
        argv = tuple(command)
        started = monotonic()
        if cancel_event is not None and cancel_event.is_set():
            return ProcessResult(argv, None, b"", b"", 0.0, False, True)
        owns_process_group = self.spawn is None
        process = await self.spawn_process(
            *argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=stderr,
        )
        if on_running is not None:
            try:
                on_running(process)
            except Exception:
                # A failing observer must not orphan the spawned child.
                with suppress(Exception):
                    await self.terminate_process(process, process_group=owns_process_group)
                raise
        try:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    self.communicate_with_cancel(process, cancel_event),
                    timeout=timeout,
                )
            except TimeoutError:
                stdout_bytes, stderr_bytes = await self.terminate_process(
                    process, process_group=owns_process_group,
                )
                return ProcessResult(
                    argv, None, stdout_bytes, stderr_bytes,
                    monotonic() - started, True, False,
                )
            except OperationCancelled:
                stdout_bytes, stderr_bytes = await self.terminate_process(
                    process, process_group=owns_process_group,
                )
                return ProcessResult(
                    argv, getattr(process, "returncode", None), stdout_bytes, stderr_bytes,
                    monotonic() - started, False, True,
                )
        except asyncio.CancelledError:
            # An external task cancellation must not orphan the child: make a
            # best-effort termination attempt, then propagate cancellation.
            try:
                await self.terminate_process(process, process_group=owns_process_group)
            except BaseException:
                pass
            raise
        duration = monotonic() - started
        return ProcessResult(
            argv, getattr(process, "returncode", None), stdout_bytes, stderr_bytes,
            duration, False, False,
        )


__all__ = [
    "OperationCancelled",
    "ProcessResult",
    "ProcessRuntime",
    "SpawnFactory",
]
