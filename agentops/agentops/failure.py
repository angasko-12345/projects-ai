"""First-class failure, repair, and recovery domain.

This module is intentionally a leaf: no I/O, no SQLite, no LLM calls.
Classification is fully deterministic (string matching + explicit flags) so
crash recovery never converts an unknown/interrupted state into success
without evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from .tasks import utc_now


class FailureCategory(StrEnum):
    AGENT_ERROR = "AGENT_ERROR"
    PROCESS_ERROR = "PROCESS_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    TEST_FAILURE = "TEST_FAILURE"
    LINT_FAILURE = "LINT_FAILURE"
    TYPECHECK_FAILURE = "TYPECHECK_FAILURE"
    BUILD_FAILURE = "BUILD_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    GIT_CONFLICT = "GIT_CONFLICT"
    DIRTY_WORKTREE = "DIRTY_WORKTREE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    REVIEW_REJECTION = "REVIEW_REJECTION"
    UNKNOWN = "UNKNOWN"


class FailureSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FailureSource(StrEnum):
    AGENT = "AGENT"
    VERIFICATION = "VERIFICATION"
    REVIEW = "REVIEW"
    WORKTREE = "WORKTREE"
    GIT = "GIT"
    MERGE = "MERGE"
    POLICY = "POLICY"
    ENVIRONMENT = "ENVIRONMENT"
    DEPENDENCY = "DEPENDENCY"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class RepairAction(StrEnum):
    RETRY_SAME_AGENT = "retry_same_agent"
    RETRY_DIFFERENT_AGENT = "retry_different_agent"
    REPAIR_IMPLEMENTATION = "repair_implementation"
    RERUN_VERIFICATION = "rerun_verification"
    REQUEST_APPROVAL = "request_approval"
    STOP = "stop"


class RecoveryState(StrEnum):
    INTERRUPTED_AGENT_EXECUTION = "interrupted_agent_execution"
    INTERRUPTED_VERIFICATION = "interrupted_verification"
    INTERRUPTED_REVIEW = "interrupted_review"
    INTERRUPTED_WORKTREE_CREATION = "interrupted_worktree_creation"
    INTERRUPTED_GIT_OPERATION = "interrupted_git_operation"
    INTERRUPTED_MERGE = "interrupted_merge"
    UNKNOWN_INTERRUPTED = "unknown_interrupted"
    RECOVERED_FAILED = "recovered_failed"
    RECOVERED_CANCELLED = "recovered_cancelled"


class InterruptionContext(StrEnum):
    AGENT_EXECUTION = "agent_execution"
    VERIFICATION = "verification"
    REVIEW = "review"
    WORKTREE_CREATION = "worktree_creation"
    GIT_OPERATION = "git_operation"
    MERGE = "merge"
    UNKNOWN = "unknown"


_INTERRUPTION_RECOVERY: dict[str, RecoveryState] = {
    InterruptionContext.AGENT_EXECUTION: RecoveryState.INTERRUPTED_AGENT_EXECUTION,
    InterruptionContext.VERIFICATION: RecoveryState.INTERRUPTED_VERIFICATION,
    InterruptionContext.REVIEW: RecoveryState.INTERRUPTED_REVIEW,
    InterruptionContext.WORKTREE_CREATION: RecoveryState.INTERRUPTED_WORKTREE_CREATION,
    InterruptionContext.GIT_OPERATION: RecoveryState.INTERRUPTED_GIT_OPERATION,
    InterruptionContext.MERGE: RecoveryState.INTERRUPTED_MERGE,
}

_INTERRUPTION_CATEGORY: dict[str, FailureCategory] = {
    InterruptionContext.AGENT_EXECUTION: FailureCategory.AGENT_ERROR,
    InterruptionContext.VERIFICATION: FailureCategory.VERIFICATION_FAILURE,
    InterruptionContext.REVIEW: FailureCategory.REVIEW_REJECTION,
    InterruptionContext.WORKTREE_CREATION: FailureCategory.PROCESS_ERROR,
    InterruptionContext.GIT_OPERATION: FailureCategory.PROCESS_ERROR,
    InterruptionContext.MERGE: FailureCategory.GIT_CONFLICT,
}


# Cap for free-text process output stored inside structured evidence.  The
# cap bounds row size; callers additionally redact before constructing.
STDERR_PEEK_MAX_CHARS = 500


@dataclass(frozen=True)
class FailureEvidence:
    """Machine-readable failure signals captured at the point of failure.

    Structured evidence takes precedence over substring heuristics in
    :meth:`FailureClassifier.classify`.  An evidence value carrying no
    signals (see :meth:`is_empty`) is ignored so callers can always pass
    one through without changing legacy classification behavior.
    """

    source: FailureSource | str | None = None
    check_class: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    terminated: bool = False
    command: tuple[str, ...] = ()
    stderr_peek: str | None = None

    def __post_init__(self) -> None:
        # Total constructor: hostile field values degrade to None/empty
        # instead of breaking classification or persistence.
        try:
            command = tuple(self.command or ())
        except Exception:
            command = ()
        object.__setattr__(self, "command", command)
        try:
            check_class = None if self.check_class is None else str(self.check_class)
        except Exception:
            check_class = None
        object.__setattr__(self, "check_class", check_class)
        try:
            exit_code = None if self.exit_code is None else int(self.exit_code)
        except Exception:
            exit_code = None
        object.__setattr__(self, "exit_code", exit_code)

    def is_empty(self) -> bool:
        """True when the evidence carries no classification signal."""
        return (
            self.exit_code is None
            and not (self.timed_out or self.cancelled or self.terminated)
            and not (self.check_class or "").strip()
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-safe mapping (never raises for hostile field values)."""
        try:
            source = self.source.value if isinstance(self.source, FailureSource) else self.source
        except Exception:
            source = None
        try:
            peek = self.stderr_peek[:STDERR_PEEK_MAX_CHARS] if self.stderr_peek else None
        except Exception:
            peek = None
        try:
            command = [str(item) for item in self.command]
        except Exception:
            command = []
        return {
            "source": source,
            "check_class": self.check_class,
            "exit_code": self.exit_code,
            "timed_out": bool(self.timed_out),
            "cancelled": bool(self.cancelled),
            "terminated": bool(self.terminated),
            "command": command,
            "stderr_peek": peek,
        }


@dataclass(frozen=True)
class Failure:
    id: str
    workflow_id: str | None
    task_id: str | None
    agent_run_id: str | None
    source: FailureSource
    category: FailureCategory
    severity: FailureSeverity
    retryable: bool
    repairable: bool
    evidence: str | None = None
    primary_error: str | None = None
    verification_run_id: str | None = None
    recommended_action: RepairAction = RepairAction.STOP
    attempt: int = 1
    repair_cycle: int = 0
    recovery_state: RecoveryState | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    structured_evidence: dict[str, object] | None = None


@dataclass(frozen=True)
class ClassificationResult:
    category: FailureCategory
    severity: FailureSeverity
    retryable: bool
    repairable: bool
    recommended_action: RepairAction
    source: FailureSource


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    max_repair_cycles: int = 1
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    backoff_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be at least one.")
        if self.max_repair_cycles < 0:
            raise ValueError("RetryPolicy.max_repair_cycles must be zero or greater.")
        if self.backoff_base_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("RetryPolicy backoff bounds must be non-negative.")
        if self.backoff_factor < 1.0:
            raise ValueError("RetryPolicy.backoff_factor must be at least one.")

    def should_retry_attempt(self, attempt: int, failure: Failure | ClassificationResult) -> bool:
        retryable = failure.retryable if isinstance(failure, (Failure, ClassificationResult)) else False
        return bool(retryable) and attempt < self.max_attempts

    def should_repair(self, repair_cycle: int, failure: Failure | ClassificationResult) -> bool:
        repairable = failure.repairable if isinstance(failure, (Failure, ClassificationResult)) else False
        return bool(repairable) and repair_cycle < self.max_repair_cycles

    def backoff_for(self, attempt: int) -> float:
        if attempt <= 1:
            return 0.0
        delay = self.backoff_base_seconds * (self.backoff_factor ** (attempt - 2))
        return min(delay, self.backoff_max_seconds)

    def sleep(self, attempt: int) -> None:
        delay = self.backoff_for(attempt)
        if delay > 0:
            time.sleep(delay)

    async def asleep(self, attempt: int) -> None:
        import asyncio

        delay = self.backoff_for(attempt)
        if delay > 0:
            await asyncio.sleep(delay)


@dataclass(frozen=True)
class RepairPlan:
    failure_id: str
    action: RepairAction
    reason: str
    next_attempt: int
    next_repair_cycle: int
    inherit_context: bool = True
    requires_approval: bool = False
    parent_run_id: str | None = None
    verification_run_id: str | None = None
    previous_evidence: str | None = None


def _contains(haystack: str, *needles: str) -> bool:
    lowered = haystack.lower()
    return any(needle in lowered for needle in needles)


class FailureClassifier:
    """Deterministic error classification. Never calls an LLM."""

    @staticmethod
    def classify(
        *,
        source: FailureSource | str = FailureSource.UNKNOWN,
        exit_code: int | None = None,
        timed_out: bool = False,
        cancelled: bool = False,
        terminated: bool = False,
        error: str | None = None,
        output: str | None = None,
        check_class: str | None = None,
        role: str | None = None,
        agent_available: bool = True,
        evidence: FailureEvidence | None = None,
    ) -> ClassificationResult:
        src = source if isinstance(source, FailureSource) else FailureSource(str(source))
        text = " ".join(part for part in (error or "", output or "") if part).strip()
        lowered = text.lower()

        if evidence is not None and not evidence.is_empty():
            structured = FailureClassifier._classify_structured(src, evidence)
            if structured is not None:
                return structured

        if cancelled or _contains(lowered, "operationcancelled", "task was cancelled", "taskcancelled"):
            return ClassificationResult(
                FailureCategory.CANCELLATION, FailureSeverity.MEDIUM,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if timed_out or _contains(lowered, "timed out", "timeout"):
            # Dependency install timeouts are dependency failures; otherwise generic timeout.
            if _contains(lowered, "pip", "npm", "module", "package", "dependency"):
                return ClassificationResult(
                    FailureCategory.DEPENDENCY_FAILURE, FailureSeverity.HIGH,
                    retryable=True, repairable=True,
                    recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
                )
            return ClassificationResult(
                FailureCategory.TIMEOUT, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if terminated or (exit_code is not None and exit_code < 0):
            return ClassificationResult(
                FailureCategory.PROCESS_ERROR, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if _contains(lowered, "merge conflict", "conflicting", "automatic merge failed",
                     "merge_head", "unmerged"):
            return ClassificationResult(
                FailureCategory.GIT_CONFLICT, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REQUEST_APPROVAL, source=src,
            )
        if _contains(lowered, "dirty worktree", "dirty work tree", "worktree",
                     "already exists", "work tree"):
            # Distinguish conflict (handled above) from dirty-tree conditions.
            if _contains(lowered, "dirty", "uncommitted", "unstaged", "already exists"):
                return ClassificationResult(
                    FailureCategory.DIRTY_WORKTREE, FailureSeverity.MEDIUM,
                    retryable=True, repairable=True,
                    recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
                )
        if _contains(lowered, "allowlist", "not allowlisted", "not allowed",
                     "outside the working directory", "refusing to", "approval required",
                     "policy"):
            return ClassificationResult(
                FailureCategory.POLICY_VIOLATION, FailureSeverity.CRITICAL,
                retryable=False, repairable=False,
                recommended_action=RepairAction.REQUEST_APPROVAL, source=src,
            )
        if _contains(lowered, "no installed agent", "agent is unavailable", "agent unavailable",
                     "executable not found", "no such file", "spawn", "enoent"):
            return ClassificationResult(
                FailureCategory.ENVIRONMENT_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=False,
                recommended_action=RepairAction.REQUEST_APPROVAL, source=src,
            )
        if _contains(lowered, "no module named", "modulenotfound", "importerror",
                     "cannot find module", "pip install", "npm err", "missing dependency",
                     "dependency"):
            return ClassificationResult(
                FailureCategory.DEPENDENCY_FAILURE, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if _contains(lowered, "permission denied", "eacces", "eperm"):
            # Permission errors are environment failures that need a human to
            # fix filesystem permissions first; they must not stop as UNKNOWN.
            return ClassificationResult(
                FailureCategory.ENVIRONMENT_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REQUEST_APPROVAL, source=src,
            )
        if _contains(lowered, "environ", "systemroot", "path"):
            # Generic environment problems (distinct from missing modules above).
            if _contains(lowered, "systemroot", "environment variable", "environ"):
                return ClassificationResult(
                    FailureCategory.ENVIRONMENT_FAILURE, FailureSeverity.HIGH,
                    retryable=True, repairable=True,
                    recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
                )
        # Verification check classes map deterministically.
        normalized_check = (check_class or "").lower()
        if normalized_check in {"tests", "test"} or _contains(lowered, "pytest", "unittest", "jest failed",
                                                              "test failed", "assertionerror"):
            # Only claim TEST_FAILURE when there is test-shaped evidence or an
            # explicit tests check class; otherwise fall through.
            if normalized_check in {"tests", "test"} or _contains(
                lowered, "pytest", "unittest", "test failed", "assertionerror", "failing test"
            ):
                return ClassificationResult(
                    FailureCategory.TEST_FAILURE, FailureSeverity.HIGH,
                    retryable=False, repairable=True,
                    recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
                )
        if normalized_check in {"lint"} or _contains(lowered, "ruff", "flake8", "eslint", "lint"):
            return ClassificationResult(
                FailureCategory.LINT_FAILURE, FailureSeverity.MEDIUM,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if normalized_check in {"type_checking", "typecheck", "type-check"} or _contains(
            lowered, "mypy", "pyright", "type error", "typecheck"
        ):
            return ClassificationResult(
                FailureCategory.TYPECHECK_FAILURE, FailureSeverity.MEDIUM,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if normalized_check in {"build"} or _contains(lowered, "pyinstaller", "webpack", "tsc -b",
                                                      "build failed", "compilation"):
            return ClassificationResult(
                FailureCategory.BUILD_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if normalized_check in {"formatting", "format"} or _contains(lowered, "black --check", "prettier"):
            return ClassificationResult(
                FailureCategory.LINT_FAILURE, FailureSeverity.LOW,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if normalized_check in {"custom"} or _contains(lowered, "verification", "required check",
                                                       "nonzero_exit", "malformed_output"):
            # A failing verification check without a more specific class.
            if normalized_check or _contains(lowered, "verification", "nonzero_exit", "malformed_output",
                                             "required"):
                return ClassificationResult(
                    FailureCategory.VERIFICATION_FAILURE, FailureSeverity.HIGH,
                    retryable=False, repairable=True,
                    recommended_action=RepairAction.RERUN_VERIFICATION, source=src,
                )
        if (role or "").lower() == "review" or _contains(lowered, "review did not pass",
                                                         "review rejection", "changes requested"):
            return ClassificationResult(
                FailureCategory.REVIEW_REJECTION, FailureSeverity.MEDIUM,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=src,
            )
        if _contains(lowered, "interrupted", "process was killed", "process did not exit"):
            return ClassificationResult(
                FailureCategory.PROCESS_ERROR, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if not agent_available or _contains(lowered, "no installed agent supports role"):
            return ClassificationResult(
                FailureCategory.ENVIRONMENT_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=False,
                recommended_action=RepairAction.RETRY_DIFFERENT_AGENT, source=src,
            )
        if exit_code is not None and exit_code != 0:
            return ClassificationResult(
                FailureCategory.AGENT_ERROR, FailureSeverity.MEDIUM,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if _contains(lowered, "exit code") and _contains(
            lowered, "1", "2", "3", "4", "5", "6", "7", "8", "9",
        ):
            # Textual nonzero-exit evidence without a structured exit code
            # (e.g. relayed agent transcripts) is still an agent error.
            return ClassificationResult(
                FailureCategory.AGENT_ERROR, FailureSeverity.MEDIUM,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=src,
            )
        if text.strip():
            # Non-empty but unrecognized evidence stays UNKNOWN and stops the
            # line: never invent success or a specific retry from noise.
            return ClassificationResult(
                FailureCategory.UNKNOWN, FailureSeverity.MEDIUM,
                retryable=False, repairable=False,
                recommended_action=RepairAction.STOP, source=src,
            )
        return ClassificationResult(
            FailureCategory.UNKNOWN, FailureSeverity.MEDIUM,
            retryable=False, repairable=False,
            recommended_action=RepairAction.STOP, source=src,
        )

    @staticmethod
    def _classify_structured(
        source: FailureSource, evidence: FailureEvidence,
    ) -> ClassificationResult | None:
        """Classify from machine-readable signals before string heuristics.

        Outcome payloads mirror the substring branch exactly, so behavior only
        changes where structured evidence wins.  Returns None when the
        evidence carries no decisive signal so callers fall through to text.
        """
        if evidence.cancelled:
            return ClassificationResult(
                FailureCategory.CANCELLATION, FailureSeverity.MEDIUM,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=source,
            )
        if evidence.timed_out:
            # Specified precedence: an authoritative timeout signal wins even
            # over dependency-flavored context (legacy text rules alone would
            # say DEPENDENCY_FAILURE for e.g. "pip install timeout").
            return ClassificationResult(
                FailureCategory.TIMEOUT, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=source,
            )
        if evidence.terminated or (evidence.exit_code is not None and evidence.exit_code < 0):
            return ClassificationResult(
                FailureCategory.PROCESS_ERROR, FailureSeverity.HIGH,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=source,
            )
        normalized_check = (evidence.check_class or "").strip().lower()
        if source is FailureSource.VERIFICATION and not normalized_check:
            # Legacy verification commands carry an exit code but no check
            # class: persist the evidence, but do not invent an agent error —
            # legacy text rules keep deciding the category.
            return None
        if normalized_check in {"tests", "test"}:
            return ClassificationResult(
                FailureCategory.TEST_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=source,
            )
        if normalized_check in {"lint"}:
            return ClassificationResult(
                FailureCategory.LINT_FAILURE, FailureSeverity.MEDIUM,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=source,
            )
        if normalized_check in {"type_checking", "typecheck", "type-check"}:
            return ClassificationResult(
                FailureCategory.TYPECHECK_FAILURE, FailureSeverity.MEDIUM,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=source,
            )
        if normalized_check in {"build"}:
            return ClassificationResult(
                FailureCategory.BUILD_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=source,
            )
        if normalized_check in {"formatting", "format"}:
            return ClassificationResult(
                FailureCategory.LINT_FAILURE, FailureSeverity.LOW,
                retryable=False, repairable=True,
                recommended_action=RepairAction.REPAIR_IMPLEMENTATION, source=source,
            )
        if normalized_check in {"custom"}:
            return ClassificationResult(
                FailureCategory.VERIFICATION_FAILURE, FailureSeverity.HIGH,
                retryable=False, repairable=True,
                recommended_action=RepairAction.RERUN_VERIFICATION, source=source,
            )
        if evidence.exit_code is not None and evidence.exit_code != 0:
            return ClassificationResult(
                FailureCategory.AGENT_ERROR, FailureSeverity.MEDIUM,
                retryable=True, repairable=True,
                recommended_action=RepairAction.RETRY_SAME_AGENT, source=source,
            )
        return None

    @staticmethod
    def classify_interruption(
        context: InterruptionContext | str,
        detail: str | None = None,
    ) -> tuple[RecoveryState, FailureCategory, RepairAction]:
        try:
            normalized = context if isinstance(context, InterruptionContext) else InterruptionContext(str(context))
        except ValueError:
            normalized = InterruptionContext.UNKNOWN
        if normalized is InterruptionContext.UNKNOWN:
            return (RecoveryState.UNKNOWN_INTERRUPTED, FailureCategory.UNKNOWN, RepairAction.STOP)
        key = normalized.value
        state = _INTERRUPTION_RECOVERY.get(key, RecoveryState.UNKNOWN_INTERRUPTED)
        category = _INTERRUPTION_CATEGORY.get(key, FailureCategory.UNKNOWN)
        if normalized is InterruptionContext.MERGE:
            action = RepairAction.REQUEST_APPROVAL
        elif normalized in {InterruptionContext.VERIFICATION}:
            action = RepairAction.RERUN_VERIFICATION
        elif normalized in {InterruptionContext.REVIEW}:
            action = RepairAction.REPAIR_IMPLEMENTATION
        else:
            action = RepairAction.RETRY_SAME_AGENT
        _ = detail  # preserved as evidence by the caller; never alters the state.
        return (state, category, action)

    @staticmethod
    def plan_repair(
        failure: Failure,
        *,
        attempt: int,
        repair_cycle: int,
        policy: RetryPolicy,
        alternate_agent_available: bool = False,
        approval_granted: bool = False,
    ) -> RepairPlan:
        if failure.category is FailureCategory.CANCELLATION:
            # Cancellations still consume the attempt budget: an exhausted
            # budget stops instead of looping forever on repeated planning.
            if policy.should_retry_attempt(attempt, failure):
                return RepairPlan(
                    failure_id=failure.id, action=RepairAction.RETRY_SAME_AGENT,
                    reason="Cancellation is retryable once the cancel request is cleared.",
                    next_attempt=attempt + 1, next_repair_cycle=repair_cycle,
                    parent_run_id=failure.agent_run_id,
                    verification_run_id=failure.verification_run_id,
                    previous_evidence=failure.evidence,
                )
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.STOP,
                reason="Retry budget exhausted for the cancelled attempt; stop permanently.",
                next_attempt=attempt, next_repair_cycle=repair_cycle,
                inherit_context=False,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        if failure.category is FailureCategory.POLICY_VIOLATION:
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.REQUEST_APPROVAL,
                reason="Policy violations require human approval before any retry.",
                next_attempt=attempt, next_repair_cycle=repair_cycle,
                inherit_context=False, requires_approval=True,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        if failure.category is FailureCategory.GIT_CONFLICT:
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.REQUEST_APPROVAL,
                reason="Merge conflicts preserve the worktree and need human resolution.",
                next_attempt=attempt, next_repair_cycle=repair_cycle,
                requires_approval=True,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        if failure.category in {
            FailureCategory.TEST_FAILURE, FailureCategory.LINT_FAILURE,
            FailureCategory.TYPECHECK_FAILURE, FailureCategory.BUILD_FAILURE,
            FailureCategory.VERIFICATION_FAILURE, FailureCategory.REVIEW_REJECTION,
        }:
            if policy.should_repair(repair_cycle, failure):
                action = (
                    RepairAction.RERUN_VERIFICATION
                    if failure.category is FailureCategory.VERIFICATION_FAILURE
                    else RepairAction.REPAIR_IMPLEMENTATION
                )
                return RepairPlan(
                    failure_id=failure.id, action=action,
                    reason=f"{failure.category.value} is repairable within the repair budget.",
                    next_attempt=attempt, next_repair_cycle=repair_cycle + 1,
                    parent_run_id=failure.agent_run_id,
                    verification_run_id=failure.verification_run_id,
                    previous_evidence=failure.evidence,
                )
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.STOP,
                reason="Repair budget exhausted; stop permanently.",
                next_attempt=attempt, next_repair_cycle=repair_cycle,
                inherit_context=False,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        if policy.should_retry_attempt(attempt, failure):
            if alternate_agent_available and failure.category in {
                FailureCategory.AGENT_ERROR, FailureCategory.ENVIRONMENT_FAILURE,
            }:
                return RepairPlan(
                    failure_id=failure.id, action=RepairAction.RETRY_DIFFERENT_AGENT,
                    reason="Retry with a different agent after excluding the failed one.",
                    next_attempt=attempt + 1, next_repair_cycle=repair_cycle,
                    parent_run_id=failure.agent_run_id,
                    verification_run_id=failure.verification_run_id,
                    previous_evidence=failure.evidence,
                )
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.RETRY_SAME_AGENT,
                reason="Retryable failure within the attempt budget.",
                next_attempt=attempt + 1, next_repair_cycle=repair_cycle,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        if failure.repairable and policy.should_repair(repair_cycle, failure) and approval_granted:
            return RepairPlan(
                failure_id=failure.id, action=RepairAction.REPAIR_IMPLEMENTATION,
                reason="Approved repair within the repair budget.",
                next_attempt=attempt, next_repair_cycle=repair_cycle + 1,
                parent_run_id=failure.agent_run_id,
                verification_run_id=failure.verification_run_id,
                previous_evidence=failure.evidence,
            )
        return RepairPlan(
            failure_id=failure.id, action=RepairAction.STOP,
            reason="No retry or repair budget remains; stop permanently.",
            next_attempt=attempt, next_repair_cycle=repair_cycle,
            inherit_context=False,
            parent_run_id=failure.agent_run_id,
            verification_run_id=failure.verification_run_id,
            previous_evidence=failure.evidence,
        )


def build_retry_prompt(base_prompt: str, failure: Failure) -> str:
    """Build inherited context for the next attempt.

    The previous error and verification evidence are appended so retries do
    not repeat the same mistake. Callers persist only hashes of the full
    prompt (see AgentRunContext); no secrets are added here.
    """

    lines = [base_prompt.rstrip()]
    lines.append("")
    lines.append(f"Previous attempt failed with {failure.category.value}.")
    if failure.primary_error:
        lines.append(f"Primary error: {failure.primary_error}")
    if failure.evidence:
        lines.append(f"Evidence: {failure.evidence}")
    if failure.verification_run_id:
        lines.append(f"Previous verification run: {failure.verification_run_id}")
    lines.append("Use this context to avoid repeating the same failure.")
    return "\n".join(lines)


def new_failure_id() -> str:
    return str(uuid4())


__all__ = [
    "Failure",
    "FailureCategory",
    "FailureSeverity",
    "FailureSource",
    "RepairAction",
    "RecoveryState",
    "InterruptionContext",
    "ClassificationResult",
    "FailureEvidence",
    "RetryPolicy",
    "RepairPlan",
    "FailureClassifier",
    "build_retry_prompt",
    "new_failure_id",
]
