"""First-class Verification Kernel domain model.

This module is intentionally a leaf: it performs no I/O and imports nothing
from the application besides timestamps.  Execution lives in
``verification_kernel.py``; persistence lives in ``state.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .tasks import utc_now


class VerificationCheckClass(StrEnum):
    TESTS = "tests"
    LINT = "lint"
    FORMATTING = "formatting"
    TYPE_CHECKING = "type_checking"
    BUILD = "build"
    CUSTOM = "custom"


class VerificationExecutionPolicy(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class VerificationProfileMode(StrEnum):
    FAIL_FAST = "fail_fast"
    CONTINUE_ON_FAILURE = "continue_on_failure"


class VerificationCheckStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class VerificationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class VerificationReportStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class VerificationCheckSpec:
    name: str
    check_class: VerificationCheckClass
    command: tuple[str, ...]
    working_directory: str | None = None
    timeout_seconds: int | None = None
    required: bool = True
    policy: VerificationExecutionPolicy = VerificationExecutionPolicy.SEQUENTIAL


@dataclass(frozen=True)
class VerificationProfile:
    name: str
    mode: VerificationProfileMode = VerificationProfileMode.FAIL_FAST
    concurrency: int = 1
    default_timeout_seconds: int = 300
    checks: tuple[VerificationCheckSpec, ...] = ()


@dataclass(frozen=True)
class VerificationCheck:
    id: str
    run_id: str
    workflow_id: str | None
    task_id: str | None
    profile_name: str
    name: str
    check_class: VerificationCheckClass
    command: tuple[str, ...]
    working_directory: str | None
    timeout_seconds: int | None
    required: bool
    policy: VerificationExecutionPolicy
    status: VerificationCheckStatus
    exit_code: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    failure_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class VerificationRun:
    id: str
    workflow_id: str | None
    task_id: str | None
    profile_name: str
    profile_snapshot: dict[str, object] = field(default_factory=dict)
    mode: VerificationProfileMode = VerificationProfileMode.FAIL_FAST
    concurrency: int = 1
    status: VerificationRunStatus = VerificationRunStatus.PENDING
    source_agent_run_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    skipped_checks: int = 0
    required_failures: int = 0
    overall_status: VerificationReportStatus | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class VerificationReport:
    id: str
    run_id: str
    workflow_id: str | None
    task_id: str | None
    profile_name: str
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    skipped_checks: int = 0
    required_failures: int = 0
    duration_seconds: float | None = None
    overall_status: VerificationReportStatus = VerificationReportStatus.FAILED
    generated_at: str = field(default_factory=utc_now)
    transcript: str = ""
    checks: tuple[VerificationCheck, ...] = ()


def profile_to_dict(profile: VerificationProfile) -> dict[str, object]:
    return {
        "name": profile.name,
        "mode": profile.mode.value,
        "concurrency": profile.concurrency,
        "default_timeout_seconds": profile.default_timeout_seconds,
        "checks": [
            {
                "name": check.name,
                "class": check.check_class.value,
                "command": list(check.command),
                "working_directory": check.working_directory,
                "timeout_seconds": check.timeout_seconds,
                "required": check.required,
                "policy": check.policy.value,
            }
            for check in profile.checks
        ],
    }


def parse_check_outcome(
    exit_code: int | None,
    timed_out: bool,
    cancelled: bool,
    raw_stdout: bytes,
    raw_stderr: bytes,
) -> tuple[VerificationCheckStatus, str | None]:
    """Deterministically map process output to a check status.

    Exit codes remain authoritative.  Output that cannot be decoded as UTF-8
    is treated as malformed evidence and fails the check even when the
    process exited zero, because the verification artifact is unreliable.
    """

    if cancelled:
        return VerificationCheckStatus.CANCELLED, "cancelled"
    if timed_out:
        return VerificationCheckStatus.TIMED_OUT, "timeout"
    if exit_code is None:
        return VerificationCheckStatus.FAILED, "unknown_exit"
    try:
        raw_stdout.decode("utf-8")
        raw_stderr.decode("utf-8")
    except UnicodeDecodeError:
        return VerificationCheckStatus.FAILED, "malformed_output"
    if exit_code == 0:
        return VerificationCheckStatus.PASSED, None
    return VerificationCheckStatus.FAILED, "nonzero_exit"


__all__ = [
    "VerificationCheck",
    "VerificationCheckClass",
    "VerificationCheckSpec",
    "VerificationCheckStatus",
    "VerificationExecutionPolicy",
    "VerificationProfile",
    "VerificationProfileMode",
    "VerificationReport",
    "VerificationReportStatus",
    "VerificationRun",
    "VerificationRunStatus",
    "parse_check_outcome",
    "profile_to_dict",
]
