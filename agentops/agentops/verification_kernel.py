"""Deterministic Verification Kernel executor.

The kernel runs configured verification profiles without executing agent
prompts.  Only explicitly configured commands are executed, always without a
shell, and check working directories are contained inside the operation
working directory.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from .logging import LogManager
from .runner import AgentRunner, OperationCancelled
from .tasks import utc_now
from .verification_model import (
    VerificationCheck,
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationCheckStatus,
    VerificationExecutionPolicy,
    VerificationProfile,
    VerificationProfileMode,
    VerificationReport,
    VerificationReportStatus,
    VerificationRun,
    VerificationRunStatus,
    parse_check_outcome,
    profile_to_dict,
)

ProcessFactory = Callable[..., Awaitable[Any]]

_MAX_TRANSCRIPT_OUTPUT = 8000


def _summarize_counts(checks: list[VerificationCheck]) -> dict[str, int]:
    passed = sum(1 for check in checks if check.status is VerificationCheckStatus.PASSED)
    failed = sum(
        1 for check in checks
        if check.status in {VerificationCheckStatus.FAILED, VerificationCheckStatus.TIMED_OUT}
    )
    skipped = sum(1 for check in checks if check.status is VerificationCheckStatus.SKIPPED)
    required_failures = sum(
        1 for check in checks
        if check.required and check.status in {
            VerificationCheckStatus.FAILED,
            VerificationCheckStatus.TIMED_OUT,
            VerificationCheckStatus.CANCELLED,
        }
    )
    return {
        "total": len(checks),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "required_failures": required_failures,
    }


def _truncate(text: str, limit: int = _MAX_TRANSCRIPT_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} characters]..."


class VerificationKernel:
    def __init__(
        self,
        profiles: dict[str, VerificationProfile] | None = None,
        default_profile: str | None = None,
        legacy_commands: tuple[tuple[str, ...], ...] = (),
        legacy_timeout_seconds: int = 300,
        pass_env_names: tuple[str, ...] = (),
        pass_env_prefixes: tuple[str, ...] = (),
        logs: LogManager | None = None,
        state: Any | None = None,
        process_factory: ProcessFactory | None = None,
    ):
        self._profiles = dict(profiles or {})
        self._default_profile = default_profile
        self._legacy_commands = tuple(legacy_commands)
        self._legacy_timeout = legacy_timeout_seconds
        self._pass_env_names = pass_env_names
        self._pass_env_prefixes = pass_env_prefixes
        self._logs = logs
        self._state = state
        self._spawn = process_factory or asyncio.create_subprocess_exec
        if default_profile is not None and default_profile not in self._profiles:
            raise ValueError(f"Unknown verification profile: {default_profile}.")

    def resolve_profile(self, name: str | None = None) -> VerificationProfile:
        if name is not None:
            try:
                return self._profiles[name]
            except KeyError as error:
                raise ValueError(f"Unknown verification profile: {name}.") from error
        if self._default_profile is not None:
            return self._profiles[self._default_profile]
        if len(self._profiles) == 1:
            return next(iter(self._profiles.values()))
        if self._profiles:
            raise ValueError("A verification profile name is required when multiple profiles are configured.")
        return VerificationProfile(
            name="legacy",
            mode=VerificationProfileMode.FAIL_FAST,
            concurrency=1,
            default_timeout_seconds=self._legacy_timeout,
            checks=tuple(
                VerificationCheckSpec(
                    name=f"legacy-check-{index + 1}",
                    check_class=VerificationCheckClass.CUSTOM,
                    command=command,
                    timeout_seconds=self._legacy_timeout,
                    required=True,
                    policy=VerificationExecutionPolicy.SEQUENTIAL,
                )
                for index, command in enumerate(self._legacy_commands)
            ),
        )

    @staticmethod
    def _execution_groups(profile: VerificationProfile) -> list[list[VerificationCheckSpec]]:
        groups: list[list[VerificationCheckSpec]] = []
        for spec in profile.checks:
            if spec.policy is VerificationExecutionPolicy.PARALLEL and groups and all(
                item.policy is VerificationExecutionPolicy.PARALLEL for item in groups[-1]
            ):
                groups[-1].append(spec)
            elif spec.policy is VerificationExecutionPolicy.PARALLEL:
                groups.append([spec])
            else:
                groups.append([spec])
        return groups

    @staticmethod
    def _resolve_check_dir(base: Path, check_dir: str | None) -> Path:
        candidate = (base / check_dir).resolve() if check_dir else base
        if not candidate.is_relative_to(base):
            raise ValueError(f"Refusing to verify outside the working directory: {check_dir}.")
        return candidate

    async def run_verification(
        self,
        workflow_id: str | None,
        task_id: str | None,
        working_directory: str | Path,
        source_agent_run_id: str | None = None,
        profile_name: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> VerificationReport:
        profile = self.resolve_profile(profile_name)
        base = Path(working_directory).resolve()
        snapshot = profile_to_dict(profile)
        started = monotonic()
        run_id = str(uuid4())
        if self._state is not None:
            record = self._state.create_verification_run(workflow_id, task_id, profile, source_agent_run_id)
            run_id = record.id
            self._state.start_verification_run(run_id)

        specs = list(profile.checks)
        names = [spec.name for spec in specs]
        if len(set(names)) != len(names):
            raise ValueError("Verification profile checks need unique names.")
        checks: dict[str, VerificationCheck] = {}
        texts: dict[str, dict[str, str]] = {}
        if self._state is not None:
            for spec in specs:
                timeout = spec.timeout_seconds or profile.default_timeout_seconds
                # Persist the declared directory; containment is enforced at
                # execution time so the stored value never masks a rejection.
                check_dir = spec.working_directory or str(base)
                created = self._state.create_verification_check(
                    run_id, workflow_id, task_id, profile.name, spec.name,
                    spec.check_class.value, spec.command, check_dir, timeout,
                    spec.required, spec.policy.value,
                )
                checks[spec.name] = created
        else:
            now = utc_now()
            for spec in specs:
                checks[spec.name] = VerificationCheck(
                    id=str(uuid4()), run_id=run_id, workflow_id=workflow_id, task_id=task_id,
                    profile_name=profile.name, name=spec.name, check_class=spec.check_class,
                    command=spec.command, working_directory=spec.working_directory,
                    timeout_seconds=spec.timeout_seconds or profile.default_timeout_seconds,
                    required=spec.required, policy=spec.policy,
                    status=VerificationCheckStatus.PENDING, created_at=now, updated_at=now,
                )

        stop = False
        try:
            for group in self._execution_groups(profile):
                if stop or (cancel_event is not None and cancel_event.is_set()):
                    self._skip_specs(group, checks, texts, "cancelled")
                    stop = True
                    continue
                await self._run_group(profile, base, group, checks, texts, cancel_event)
                group_checks = [checks[spec.name] for spec in group]
                if profile.mode is VerificationProfileMode.FAIL_FAST and any(
                    check.status is not VerificationCheckStatus.PASSED for check in group_checks
                ):
                    stop = True
        except asyncio.CancelledError:
            # External cancellation must never strand the run RUNNING: close
            # out every unfinished check, persist a CANCELLED report, then
            # re-raise so callers observe the cancellation.
            for spec in specs:
                check = checks[spec.name]
                if check.status in {VerificationCheckStatus.PENDING, VerificationCheckStatus.RUNNING}:
                    self._finish(checks, texts, spec, VerificationCheckStatus.CANCELLED,
                                 check.exit_code, None, b"", b"", "cancelled")
            ordered = [checks[spec.name] for spec in specs]
            counts = _summarize_counts(ordered)
            duration = monotonic() - started
            report = VerificationReport(
                id=str(uuid4()), run_id=run_id, workflow_id=workflow_id, task_id=task_id,
                profile_name=profile.name, total_checks=counts["total"],
                passed_checks=counts["passed"], failed_checks=counts["failed"],
                skipped_checks=counts["skipped"], required_failures=counts["required_failures"],
                duration_seconds=duration, overall_status=VerificationReportStatus.CANCELLED,
                transcript=self._transcript(profile, VerificationReportStatus.CANCELLED,
                                            counts, duration, ordered, texts),
                checks=tuple(ordered),
            )
            if self._state is not None:
                self._state.finish_verification_run(
                    run_id, VerificationRunStatus.CANCELLED, VerificationReportStatus.CANCELLED,
                    counts["total"], counts["passed"], counts["failed"], counts["skipped"],
                    counts["required_failures"], duration)
                self._state.create_verification_report(report)
            raise

        ordered = [checks[spec.name] for spec in specs]
        counts = _summarize_counts(ordered)
        cancelled = (cancel_event is not None and cancel_event.is_set()) or any(
            check.status is VerificationCheckStatus.CANCELLED for check in ordered
        )
        if cancelled:
            overall = VerificationReportStatus.CANCELLED
            run_status = VerificationRunStatus.CANCELLED
        elif counts["required_failures"]:
            required_failed = any(
                check.required and check.status is VerificationCheckStatus.FAILED for check in ordered
            )
            overall = (
                VerificationReportStatus.FAILED if required_failed
                else VerificationReportStatus.TIMED_OUT
            )
            run_status = (
                VerificationRunStatus.FAILED if required_failed
                else VerificationRunStatus.TIMED_OUT
            )
        else:
            overall = VerificationReportStatus.PASSED
            run_status = VerificationRunStatus.COMPLETED
        duration = monotonic() - started
        transcript = self._transcript(profile, overall, counts, duration, ordered, texts)
        report = VerificationReport(
            id=str(uuid4()),
            run_id=run_id,
            workflow_id=workflow_id,
            task_id=task_id,
            profile_name=profile.name,
            total_checks=counts["total"],
            passed_checks=counts["passed"],
            failed_checks=counts["failed"],
            skipped_checks=counts["skipped"],
            required_failures=counts["required_failures"],
            duration_seconds=duration,
            overall_status=overall,
            transcript=transcript,
            checks=tuple(ordered),
        )
        if self._state is not None:
            self._state.finish_verification_run(
                run_id, run_status, overall, counts["total"], counts["passed"],
                counts["failed"], counts["skipped"], counts["required_failures"], duration,
            )
            self._state.create_verification_report(report)
        return report

    def _skip_specs(
        self,
        specs: list[VerificationCheckSpec],
        checks: dict[str, VerificationCheck],
        texts: dict[str, dict[str, str]],
        reason: str,
    ) -> None:
        for spec in specs:
            check = checks[spec.name]
            if check.status is not VerificationCheckStatus.PENDING:
                continue
            texts[spec.name] = {"stdout": "", "stderr": ""}
            if self._state is not None:
                checks[spec.name] = self._state.finish_verification_check(
                    check.id, VerificationCheckStatus.SKIPPED, failure_reason=reason)
            else:
                checks[spec.name] = VerificationCheck(
                    **{**check.__dict__, "status": VerificationCheckStatus.SKIPPED,
                       "failure_reason": reason})

    async def _run_group(
        self,
        profile: VerificationProfile,
        base: Path,
        group: list[VerificationCheckSpec],
        checks: dict[str, VerificationCheck],
        texts: dict[str, dict[str, str]],
        cancel_event: threading.Event | None,
    ) -> None:
        if len(group) == 1:
            await self._execute_check(profile, base, group[0], checks, texts, cancel_event)
            return
        semaphore = asyncio.Semaphore(max(1, min(profile.concurrency, len(group))))

        async def limited(spec: VerificationCheckSpec) -> None:
            async with semaphore:
                await self._execute_check(profile, base, spec, checks, texts, cancel_event)

        pending = {asyncio.create_task(limited(spec)) for spec in group}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    await finished
                # Only cancel siblings for terminal failures: RUNNING/PENDING
                # siblings must not trigger cancellation when a finisher passed.
                if profile.mode is VerificationProfileMode.FAIL_FAST and any(
                    checks[spec.name].status in {
                        VerificationCheckStatus.FAILED,
                        VerificationCheckStatus.TIMED_OUT,
                        VerificationCheckStatus.CANCELLED,
                    } for spec in group
                ):
                    for remaining in pending:
                        remaining.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    break
        finally:
            for remaining in pending:
                if not remaining.done():
                    remaining.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _execute_check(
        self,
        profile: VerificationProfile,
        base: Path,
        spec: VerificationCheckSpec,
        checks: dict[str, VerificationCheck],
        texts: dict[str, dict[str, str]],
        cancel_event: threading.Event | None,
    ) -> None:
        check = checks[spec.name]
        if self._state is not None:
            try:
                check = self._state.start_verification_check(check.id)
                checks[spec.name] = check
            except (KeyError, ValueError):
                return
        else:
            check = VerificationCheck(**{**check.__dict__, "status": VerificationCheckStatus.RUNNING})
            checks[spec.name] = check
        if cancel_event is not None and cancel_event.is_set():
            self._finish(checks, texts, spec, VerificationCheckStatus.SKIPPED, None, None,
                         b"", b"", "cancelled")
            return
        try:
            check_dir = self._resolve_check_dir(base, spec.working_directory)
        except ValueError as error:
            self._finish(checks, texts, spec, VerificationCheckStatus.FAILED, None, None,
                         b"", b"", f"invalid_working_directory: {error}")
            return
        timeout = spec.timeout_seconds or profile.default_timeout_seconds
        process = None
        started = monotonic()
        timed_out = False
        cancelled = False
        raw_stdout, raw_stderr = b"", b""
        try:
            process = await self._spawn(
                *spec.command,
                cwd=str(check_dir),
                env=AgentRunner._environment(self._pass_env_names, self._pass_env_prefixes),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(
                    AgentRunner._communicate_with_cancel(process, cancel_event),
                    timeout=timeout,
                )
            except TimeoutError:
                timed_out = True
                raw_stdout, raw_stderr = await AgentRunner.terminate(process)
            except OperationCancelled:
                cancelled = True
                raw_stdout, raw_stderr = await AgentRunner.terminate(process)
        except OperationCancelled:
            cancelled = True
            if process is not None:
                try:
                    raw_stdout, raw_stderr = await AgentRunner.terminate(process)
                except Exception:
                    pass
        except asyncio.CancelledError:
            cancelled = True
            if process is not None:
                try:
                    raw_stdout, raw_stderr = await AgentRunner.terminate(process)
                except Exception:
                    pass
            self._finish(checks, texts, spec, VerificationCheckStatus.CANCELLED, None,
                         monotonic() - started, raw_stdout, raw_stderr, "cancelled")
            raise
        except (OSError, asyncio.TimeoutError) as error:
            self._finish(checks, texts, spec, VerificationCheckStatus.FAILED, None,
                         monotonic() - started, b"", b"", f"process_error: {error}")
            return
        duration = monotonic() - started
        exit_code = None if timed_out else (process.returncode if process is not None else None)
        status, reason = parse_check_outcome(exit_code, timed_out, cancelled, raw_stdout, raw_stderr)
        self._finish(checks, texts, spec, status, exit_code, duration, raw_stdout, raw_stderr, reason)

    def _finish(
        self,
        checks: dict[str, VerificationCheck],
        texts: dict[str, dict[str, str]],
        spec: VerificationCheckSpec,
        status: VerificationCheckStatus,
        exit_code: int | None,
        duration: float | None,
        raw_stdout: bytes,
        raw_stderr: bytes,
        reason: str | None,
    ) -> None:
        stdout_text = raw_stdout.decode("utf-8", errors="replace")
        stderr_text = raw_stderr.decode("utf-8", errors="replace")
        texts[spec.name] = {"stdout": stdout_text, "stderr": stderr_text}
        check = checks[spec.name]
        stdout_path = stderr_path = None
        if self._logs is not None:
            try:
                artifacts = self._logs.write_run_artifacts(
                    check.task_id or check.run_id,
                    f"{spec.name}-{check.id[:8]}",
                    stdout_text,
                    stderr_text,
                    (
                        f"verification_profile={check.profile_name}\n"
                        f"verification_check={spec.name}\n"
                        f"check_class={spec.check_class.value}\n"
                        f"command={list(spec.command)!r}\n"
                        f"exit_code={exit_code}\n"
                        f"status={status.value}\n"
                        f"failure_reason={reason}\n"
                        f"duration_seconds={duration if duration is not None else 0.0:.3f}\n"
                    ),
                )
                stdout_path, stderr_path = str(artifacts.stdout_path), str(artifacts.stderr_path)
            except Exception:
                stdout_path = stderr_path = None
        if self._state is not None:
            try:
                checks[spec.name] = self._state.finish_verification_check(
                    check.id, status, exit_code, duration, stdout_path, stderr_path, reason)
            except (KeyError, ValueError):
                pass
        else:
            checks[spec.name] = VerificationCheck(
                **{**check.__dict__, "status": status, "exit_code": exit_code,
                   "ended_at": utc_now(), "duration_seconds": duration,
                   "stdout_path": stdout_path, "stderr_path": stderr_path,
                   "failure_reason": reason})

    @staticmethod
    def _transcript(
        profile: VerificationProfile,
        overall: VerificationReportStatus,
        counts: dict[str, int],
        duration: float,
        checks: list[VerificationCheck],
        texts: dict[str, dict[str, str]],
    ) -> str:
        lines = [
            f"Verification profile '{profile.name}': {overall.value} "
            f"({counts['passed']}/{counts['total']} passed, {counts['failed']} failed, "
            f"{counts['skipped']} skipped, {counts['required_failures']} required failures, "
            f"{duration:.1f}s)."
        ]
        for check in checks:
            lines.append(
                f"- {check.name} [{check.check_class.value}]: {check.status.value} "
                f"(exit={check.exit_code}, required={check.required})."
            )
            if check.status is not VerificationCheckStatus.PASSED:
                output = texts.get(check.name, {})
                stdout = _truncate(output.get("stdout", ""))
                stderr = _truncate(output.get("stderr", ""))
                if stdout.strip():
                    lines.append(f"  stdout:\n{stdout.rstrip()}")
                if stderr.strip():
                    lines.append(f"  stderr:\n{stderr.rstrip()}")
                if check.failure_reason:
                    lines.append(f"  reason: {check.failure_reason}")
        return "\n".join(lines)


__all__ = ["VerificationKernel"]
