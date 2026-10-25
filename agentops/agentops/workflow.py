"""Dependency-aware task orchestration with agent fallback and repairs."""

from __future__ import annotations

import asyncio
import inspect
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .agent_run import (
    AgentRun,
    AgentRunContext,
    AgentRunMetadata,
    AgentRunObserver,
    AgentRunOutcome,
    AgentRunRelationship,
    AgentRunStatus,
    GitRunMetadataCollector,
)
from .agent_result import AgentResultStatus, coerce_agent_result, parse_agent_result
from .evidence import TaskEvidence, expected_evidence
from .execution_model import (
    StateTransitionError,
    WorkflowReadiness,
    assert_report_consistent,
    assert_task_completion,
    assert_tasks_ready,
    assess_workflow_readiness,
)
from .failure import (
    Failure,
    FailureClassifier,
    FailureEvidence,
    FailureSeverity,
    FailureSource,
    InterruptionContext,
    RepairPlan,
    RetryPolicy,
    build_retry_prompt,
    new_failure_id,
)
from .logging import redact_text
from .persistence import DegradationRecorder, event_emitter
from .security import findings_for_prompt, scan_many
from .verdict import parse_verdict
from .config import AppConfig
from .events import Event, EventSeverity, EventType
from .registry import AgentRegistry, DetectedAgent
from .repo_inspection import RepositoryInspector
from .routing import AgentRouter
from .runner import AgentRunner, OperationCancelled, RunResult
from .state import StateStore
from .tasks import Task, TaskStatus, utc_now
from .verification import Verifier
from .verification_kernel import VerificationKernel
from .verification_model import VerificationReportStatus


def _scrub_structured(value: object) -> object:
    """Recursively redact secret-looking strings in persisted evidence.

    Callers must still avoid putting raw prompts or credentials into evidence;
    this is defense-in-depth for token patterns in arbitrary mappings.
    """
    try:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {key: _scrub_structured(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_scrub_structured(item) for item in value]
    except Exception:
        return "[UNREPRESENTABLE]"
    return value



def _requires_review_verdict(role: object) -> bool:
    """Whether a task role is required to end with an explicit review verdict."""
    normalized = " ".join(
        str(role or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )
    return normalized in {"review", "security review", "security"}


def verdict_summary_for_task(task: object) -> dict[str, object] | None:
    """Small redacted verdict summary for a review/security task, if any."""
    try:
        role = getattr(task, "role", None)
        if not _requires_review_verdict(role):
            return None
        verdict = parse_verdict(getattr(task, "result", None))
        if verdict is None:
            return {"role": role, "verdict": "missing", "approved": False,
                    "blocking": [], "non_blocking": []}
        return {"role": role, "verdict": verdict.verdict, "approved": verdict.is_approved,
                "blocking": [redact_text(item)[:500] for item in verdict.blocking],
                "non_blocking": [redact_text(item)[:500] for item in verdict.non_blocking]}
    except Exception:
        return None


def _is_security_role(role: object) -> bool:
    """Whether a task role is the optional security review."""
    normalized = " ".join(
        str(role or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )
    return normalized in {"security", "security review"}


def _local_security_scan(working_directory: str | Path) -> tuple[list, bool]:
    """Redacted secret findings for the worktree diff.

    Returns ``(findings, completed)``. ``completed`` is False when the scan
    could not actually run -- git unavailable, a git error, a timeout, or an
    internal failure -- so a caller can tell "no findings" apart from
    "no scan" instead of reading a failed scan as a clean change. Never raises.
    """
    try:
        root = str(working_directory)
        parts: list[tuple[str, str | None]] = []
        completed = True
        try:
            diff = subprocess.run(
                ("git", "diff", "HEAD", "--", "."),
                cwd=root, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if diff.returncode == 0:
                if (diff.stdout or "").strip():
                    parts.append(("diff", diff.stdout))
            else:
                completed = False
        except Exception:
            completed = False
        try:
            status = subprocess.run(
                ("git", "status", "--porcelain", "--", "."),
                cwd=root, capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if status.returncode == 0:
                shown = 0
                for line in (status.stdout or "").splitlines():
                    if not line.startswith("??") or shown >= 20:
                        continue
                    shown += 1
                    name = line[2:].strip().strip('"')
                    try:
                        candidate = Path(root, name)
                        if candidate.stat().st_size > 100 * 1024:
                            continue
                        parts.append((name, candidate.read_text(encoding="utf-8", errors="replace")))
                    except (OSError, ValueError):
                        continue
            else:
                completed = False
        except Exception:
            completed = False
        if not parts:
            return [], completed
        return scan_many(parts), completed
    except Exception:
        return [], False


_ROLE_SCOPE_MAX_CHARS = 4000


def _role_scope_text(role: object, working_directory: str | Path) -> str:
    """Role contract from the target repository, when present.

    Reads ``roles/<role>.md`` under the working directory (trying the raw,
    hyphenated, and underscored role spellings). Missing files and read
    errors yield ``""`` so the built-in prompt contract applies; role files
    are never required for normal operation. Separators in the role name
    are rejected outright. Never raises.
    """
    try:
        normalized = str(role or "").strip().lower()
        if not normalized:
            return ""
        variants: list[str] = []
        for variant in (normalized, normalized.replace(" ", "-"), normalized.replace(" ", "_")):
            if variant and "/" not in variant and "\\" not in variant and variant not in {".", ".."}:
                if variant not in variants:
                    variants.append(variant)
        for variant in variants:
            try:
                candidate = Path(str(working_directory), "roles", f"{variant}.md")
                text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            except (OSError, ValueError):
                continue
            if text:
                return text[:_ROLE_SCOPE_MAX_CHARS]
        return ""
    except Exception:
        return ""


def _safe_workflow_structured(stdout: object) -> object | None:
    """Best-effort structured fallback for post-hoc run recording."""
    try:
        if not isinstance(stdout, str):
            return None
        parsed = parse_agent_result(stdout)
        result = parsed.result
        extra_warnings = tuple(dict.fromkeys((*result.warnings, *parsed.warnings)))
        metadata = dict(result.metadata)
        metadata.setdefault("parse_mode", parsed.parse_mode.value)
        from dataclasses import replace
        return replace(result, warnings=extra_warnings, metadata=metadata).to_dict()
    except Exception:
        return None


_SUCCESS_KEYS = frozenset({"success", "successful", "succeeded", "passed", "pass", "ok"})

_FALSE_LIKE = frozenset({"false", "0", "no", "n", "off", "fail", "failed", "error", "errored", ""})


def _is_verdict_only(payload: object) -> bool:
    """True for a review-verdict object, which is not an agent-status signal."""
    try:
        if not isinstance(payload, dict):
            return False
        if "verdict" not in payload or "status" in payload:
            return False
        return not (_SUCCESS_KEYS & frozenset(payload))
    except Exception:
        return False


def _payload_denies_success(payload: object) -> bool:
    """True when a structured payload explicitly reports non-success."""
    try:
        if not isinstance(payload, dict):
            return False
        for key in _SUCCESS_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if value is None:
                continue
            if isinstance(value, bool):
                if not value:
                    return True
            elif isinstance(value, (int, float)):
                if value == 0:
                    return True
            elif isinstance(value, str):
                if value.strip().lower() in _FALSE_LIKE:
                    return True
        return False
    except Exception:
        return False


def _stdout_candidate_dicts(stdout: object) -> list[dict]:
    """Best-effort JSON-object scan of agent stdout. Never raises.

    One pathological line must not abort the whole scan: every parse failure
    -- including ``RecursionError`` from pathologically nested JSON -- is
    skipped per candidate so an earlier structured signal is still seen,
    rather than collapsing the scan to "no candidates" (which the callers
    would read as the legacy plaintext path).
    """
    found: list[dict] = []
    try:
        import json as _json

        if not isinstance(stdout, str) or not stdout.strip():
            return found
        try:
            whole = _json.loads(stdout.strip())
        except (ValueError, TypeError, RecursionError):
            whole = None
        if isinstance(whole, dict):
            found.append(whole)
        for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", stdout, re.DOTALL | re.IGNORECASE):
            try:
                parsed = _json.loads(match.group(1).strip())
            except (ValueError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
        for line in reversed(stdout.splitlines()):
            text = line.strip().rstrip(",")
            if not text or not text.startswith("{"):
                continue
            try:
                parsed = _json.loads(text)
            except (ValueError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
                break
    except Exception:
        pass
    return found


def _structured_agent_status(result: object) -> AgentResultStatus | None:
    """Authoritative agent-level outcome for a runner result, if any.

    Prefers the runner-attached ``structured_result`` envelope (what the
    runner parsed from stdout); falls back to parsing ``stdout`` directly
    so runner doubles that skip the envelope are still honoured. Returns
    ``None`` when no structured payload is available -- the legacy /
    plaintext path -- so callers keep the historical exit-code behaviour.
    Review-verdict objects (``{"verdict": ...}`` without a ``status``) are
    not agent-status signals and are skipped, so a pure verdict line can
    never masquerade as agent success or failure. Never raises.
    """
    try:
        stdout_candidates = _stdout_candidate_dicts(getattr(result, "stdout", None))
        candidates = [item for item in stdout_candidates if not _is_verdict_only(item)]
        if candidates:
            coerced = coerce_agent_result(candidates[-1])
            if coerced is not None:
                return coerced.status
            return AgentResultStatus.UNKNOWN
        verdict_only_candidates = [
            item for item in stdout_candidates if _is_verdict_only(item)
        ]
        if verdict_only_candidates:
            # The only structured JSON in stdout is a review verdict
            # object, which is not an agent-status signal. The real runner
            # attaches an envelope parsed from that same stdout; its
            # UNKNOWN status is the verdict misread as an agent report,
            # not a claimed outcome. Keep it out of the gate: return None
            # (legacy path) exactly as the no-envelope doubles do.
            # 2026-10-18 regression: a compliant reviewer output (prose +
            # final verdict line, what the review prompt mandates) FAILED
            # the status gate live through workflows b901b8ac reviews.
            return None
        coerced = coerce_agent_result(getattr(result, "structured_result", None))
        if coerced is not None:
            envelope = getattr(result, "structured_result", None)
            if isinstance(envelope, dict) and _is_verdict_only(envelope):
                return None
            return coerced.status
    except Exception:
        pass
    return None


def _explicit_success_denied(result: object) -> bool:
    """True when any structured payload explicitly denies success.

    Catches ``{"success": false}``-shaped output that carries no ``status``
    field and would otherwise coerce to UNKNOWN. Verdict-only objects are
    not success signals either way and are ignored here; the verdict gate
    judges them. Never raises.
    """
    try:
        payloads: list[object] = []
        envelope = getattr(result, "structured_result", None)
        if isinstance(envelope, str):
            try:
                import json as _json

                envelope = _json.loads(envelope.strip())
            except (ValueError, TypeError, AttributeError):
                envelope = None
        if isinstance(envelope, dict):
            payloads.append(envelope)
            try:
                legacy = envelope.get("metadata", {}).get("legacy_payload")
            except (AttributeError, TypeError):
                legacy = None
            if isinstance(legacy, dict):
                payloads.append(legacy)
        payloads.extend(
            item for item in _stdout_candidate_dicts(getattr(result, "stdout", None))
            if not _is_verdict_only(item)
        )
        return any(_payload_denies_success(payload) for payload in payloads)
    except Exception:
        return False


class _RecordingObserver:
    """Delegate that reports whether the runner persisted a run."""

    def __init__(self, delegate: AgentRunObserver):
        self._delegate = delegate
        self.created_run_id: str | None = None

    def create_run(self, context: AgentRunContext) -> str | AgentRun:
        created = self._delegate.create_run(context)
        self.created_run_id = created.id if isinstance(created, AgentRun) else str(created)
        return created

    def mark_starting(self, run_id: str) -> None:
        self._delegate.mark_starting(run_id)

    def mark_running(self, run_id: str) -> None:
        self._delegate.mark_running(run_id)

    def finish_run(self, run_id: str, outcome: AgentRunOutcome) -> None:
        self._delegate.finish_run(run_id, outcome)

    def fail_run(self, run_id: str, error: BaseException, classification: str) -> None:
        self._delegate.fail_run(run_id, error, classification)


@dataclass(frozen=True)
class WorkflowResult:
    workflow_id: str
    ready: bool
    summary: str


# Task roles created by create_standard_workflow, in dependency order.
# Single source of truth: the GUI controller pins explicit agent preferences
# against this tuple when an operator chooses a fixed agent for a task.
def _is_git_working_tree(path: str | Path) -> bool:
    """True when ``path`` is inside a git working tree.

    GitRunMetadataCollector reads `git status --porcelain`, which prints nothing
    outside a repository. Without this check an empty result is ambiguous
    between "nothing changed" and "there was nothing to look at".
    """
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "--is-inside-work-tree"),
            cwd=str(path), capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        return False
    return completed.returncode == 0 and "true" in (completed.stdout or "").lower()


STANDARD_TASK_ROLES = ("architecture", "implementation", "verification", "review")

#: Per-dependency and total character budget for the handoff block in a
#: dependent agent's prompt. Dependency results are transcripts that grow with
#: the work, so without a ceiling each extra generation inflates the next prompt.
HANDOFF_RESULT_CHARS = 2000
HANDOFF_TOTAL_CHARS = 8000


class WorkflowEngine:
    def __init__(self, config: AppConfig, state: StateStore, registry: AgentRegistry,
                 runner: AgentRunner, verifier: Verifier,
                 run_observer: AgentRunObserver | None = None,
                 metadata_collector: Callable[[str | Path], AgentRunMetadata] | None = None,
                 verification_kernel: VerificationKernel | None = None,
                 router: AgentRouter | None = None,
                 degradation: DegradationRecorder | None = None,
                 repo_inspector: RepositoryInspector | None = None):
        self.config = config
        self.state = state
        self.registry = registry
        self.runner = runner
        self.verifier = verifier
        self.run_observer = run_observer
        self.metadata_collector = metadata_collector
        self.verification_kernel = verification_kernel
        self.router = router
        if self.router is None and getattr(config, "routing_enabled", True):
            self.router = AgentRouter()
        self.repo_inspector = repo_inspector if repo_inspector is not None else RepositoryInspector()
        # A8: persistence failures are classified instead of swallowed.  The
        # recorder emits a WARNING event through the same store it watches, so
        # it degrades to an in-memory entry when the store is the thing that broke.
        if degradation is None and getattr(state, "record_typed_event", None) is not None:
            degradation = DegradationRecorder(
                emit=event_emitter(state.record_typed_event))
        self.degradation = degradation if degradation is not None else DegradationRecorder()

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.config.max_attempts,
            max_repair_cycles=self.config.max_repair_cycles,
            backoff_base_seconds=getattr(self.config, "backoff_base_seconds", 1.0),
            backoff_max_seconds=getattr(self.config, "backoff_max_seconds", 30.0),
            backoff_factor=getattr(self.config, "backoff_factor", 2.0),
        )

    def plan_repair_for_failure(
        self,
        failure: Failure,
        *,
        attempt: int | None = None,
        repair_cycle: int = 0,
        alternate_agent_available: bool = False,
        approval_granted: bool = False,
    ) -> RepairPlan:
        policy = self.retry_policy
        return FailureClassifier.plan_repair(
            failure,
            attempt=attempt if attempt is not None else failure.attempt,
            repair_cycle=repair_cycle,
            policy=policy,
            alternate_agent_available=alternate_agent_available,
            approval_granted=approval_granted,
        )

    def record_failure(
        self,
        task: Task,
        classification,  # ClassificationResult
        *,
        agent_run_id: str | None = None,
        verification_run_id: str | None = None,
        evidence: str | None = None,
        primary_error: str | None = None,
        recovery_state=None,
        structured_evidence: FailureEvidence | dict[str, object] | None = None,
    ) -> Failure:
        failure = Failure(
            id=new_failure_id(),
            workflow_id=task.workflow_id,
            task_id=task.id,
            agent_run_id=agent_run_id,
            source=classification.source,
            category=classification.category,
            severity=classification.severity,
            retryable=classification.retryable,
            repairable=classification.repairable,
            # Redact before truncating: failure text carries raw stdout/stderr
            # and must not persist secrets (structured evidence is scrubbed
            # separately in _scrub_structured).
            evidence=redact_text(evidence or primary_error or task.result or "")[:4000] or None,
            primary_error=redact_text(primary_error or task.result or "")[:2000] or None,
            verification_run_id=verification_run_id,
            recommended_action=classification.recommended_action,
            attempt=max(1, task.attempts),
            repair_cycle=0,
            recovery_state=recovery_state,
            structured_evidence=_scrub_structured(
                structured_evidence.to_dict()
                if isinstance(structured_evidence, FailureEvidence)
                else dict(structured_evidence) if structured_evidence is not None else None
            ),
        )
        try:
            return self.state.create_failure(failure)
        except Exception as error:
            # A8 SAFE_TO_DEGRADE: the failure row is diagnostics. Losing it must
            # not mask the task outcome, but the loss is now visible.
            self.degradation.record(
                "failure.create", error,
                workflow_id=failure.workflow_id, task_id=failure.task_id,
            )
            return failure

    def recover_incomplete(self, workflow_id: str | None = None) -> dict[str, object]:
        """Crash recovery: detect incomplete operations and classify them.

        Runs the state-level recovery passes (agent runs, verification runs,
        tasks) and records a Failure row per recovered task with the matching
        interruption context. Never marks anything successful: recovered tasks
        stay FAILED and recovered runs stay TERMINATED/FAILED. Worktrees are
        preserved by the caller (CLI/GUI never deletes on failure/conflict).
        """
        summary = self.state.recover_all() if workflow_id is None else {
            "agent_runs": len(self.state.recover_agent_runs(workflow_id)),
            "verification_runs": len(self.state.recover_verification_runs(workflow_id)),
            "tasks": len(self.state.recover_tasks(workflow_id)),
        }
        states: list[str] = []
        tasks = self.state.list_tasks(workflow_id) if workflow_id else []
        for task in tasks:
            if task.status is not TaskStatus.FAILED:
                continue
            if task.result is None or "interrupted" not in task.result.lower():
                continue
            context = (
                InterruptionContext.VERIFICATION if task.role == "verification" else
                InterruptionContext.REVIEW if task.role == "review" else
                InterruptionContext.AGENT_EXECUTION
            )
            recovery_state, category, action = FailureClassifier.classify_interruption(context)
            # Idempotency: a repeated recovery pass must not mint a fresh
            # Failure per call for the same stranded task. The (task,
            # recovery_state) pair is the idempotency key.
            try:
                prior = self.state.list_failures(task.workflow_id, task.id, limit=50)
            except Exception:
                prior = []
            if any(getattr(item, "recovery_state", None) == recovery_state for item in prior):
                states.append(recovery_state.value)
                continue
            states.append(recovery_state.value)
            try:
                latest_run = self.state.latest_agent_run(task.workflow_id, task.id)
            except Exception:
                latest_run = None
            failure = Failure(
                id=new_failure_id(),
                workflow_id=task.workflow_id,
                task_id=task.id,
                agent_run_id=latest_run.id if latest_run is not None else None,
                source=FailureSource.SYSTEM,
                category=category,
                severity=FailureSeverity.HIGH,
                retryable=True,
                repairable=True,
                evidence=redact_text(task.result)[:4000] if task.result else None,
                primary_error="Task was interrupted before a terminal outcome was recorded.",
                verification_run_id=task.verification_run_id,
                recommended_action=action,
                attempt=max(1, task.attempts),
                recovery_state=recovery_state,
            )
            try:
                self.state.create_failure(failure)
            except Exception as error:
                # A8: this path bypasses record_failure(), so it needs its own
                # degradation record or a lost recovery row stays invisible.
                self.degradation.record(
                    "failure.create", error,
                    workflow_id=failure.workflow_id, task_id=failure.task_id,
                )
        summary["recovery_states"] = states
        return summary

    def create_standard_workflow(self, description: str) -> tuple[str, list[Task]]:
        workflow_id = self.state.create_workflow(description)
        plan = self.state.add_task(Task(f"Plan a safe implementation for: {description}", STANDARD_TASK_ROLES[0], workflow_id,
                                        max_attempts=self.config.max_attempts))
        implementation = self.state.add_task(Task(f"Implement: {description}", STANDARD_TASK_ROLES[1], workflow_id,
                                                  dependencies=(plan.id,), max_attempts=self.config.max_attempts))
        verification = self.state.add_task(Task("Run configured project verification commands.", STANDARD_TASK_ROLES[2], workflow_id,
                                                dependencies=(implementation.id,), max_attempts=1))
        # The reviewer judges the implementation, so it needs the implementation
        # result, not only the verification transcript. Both are PASSED before
        # review can be scheduled, so this adds context, not a gate.
        review = self.state.add_task(Task(f"Review the completed change for: {description}", STANDARD_TASK_ROLES[3], workflow_id,
                                          dependencies=(verification.id, implementation.id),
                                          max_attempts=self.config.max_attempts))
        tasks = [plan, implementation, verification, review]
        if getattr(self.config, "security_review", False):
            security = self.state.add_task(Task(
                f"Security-review the completed change for: {description}",
                "security", workflow_id,
                dependencies=(review.id, implementation.id),
                max_attempts=self.config.max_attempts))
            tasks.append(security)
        return workflow_id, tasks

    def create_workflow(self, description: str, specifications: list[dict[str, object]]) -> tuple[str, list[Task]]:
        """Create a user-defined dependency graph from a validated workflow file."""
        self.validate_workflow_specifications(specifications)
        identifiers: dict[str, str] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            identifiers[identifier] = str(uuid4())
        workflow_id = self.state.create_workflow(description)
        tasks: list[Task] = []
        try:
            for index, specification in enumerate(specifications):
                identifier = specification.get("id", f"task-{index + 1}")
                description_value = specification["description"]
                role = specification["role"]
                dependencies = specification.get("dependencies", [])
                task = Task(description_value, role, workflow_id, id=identifiers[identifier],
                            dependencies=tuple(identifiers[item] for item in dependencies),
                            max_attempts=self.config.max_attempts)
                tasks.append(self.state.add_task(task))
        except Exception:
            # Each StateStore write commits independently. If persistence fails
            # midway, remove the workflow and any tasks already inserted so a
            # retry never sees a half-created graph.
            self.state.delete_workflow(workflow_id)
            raise
        return workflow_id, tasks

    @classmethod
    def validate_workflow_specifications(
        cls, specifications: list[dict[str, object]],
    ) -> None:
        """Validate all task fields and graph references before any side effect."""
        if not isinstance(specifications, list) or not all(
            isinstance(specification, dict) for specification in specifications
        ):
            raise ValueError("Workflow tasks must be a list of mappings.")
        identifiers: dict[str, str] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError("Each workflow task needs a unique string id.")
            if not isinstance(specification.get("description"), str) or not isinstance(
                specification.get("role"), str
            ):
                raise ValueError("Each workflow task requires string description and role fields.")
            dependencies = specification.get("dependencies", [])
            if not isinstance(dependencies, list) or not all(
                isinstance(item, str) for item in dependencies
            ):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            identifiers[identifier] = identifier
        cls._validate_acyclic(specifications, identifiers)

    @staticmethod
    def _validate_acyclic(specifications: list[dict[str, object]], identifiers: dict[str, str]) -> None:
        dependencies_by_id: dict[str, set[str]] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            dependencies = specification.get("dependencies", [])
            if not isinstance(identifier, str) or not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            if identifier in dependencies:
                raise ValueError(f"Workflow task '{identifier}' cannot depend on itself.")
            dependencies_by_id[identifier] = set(dependencies)
        unknown = sorted({dependency for dependencies in dependencies_by_id.values() for dependency in dependencies if dependency not in identifiers})
        if unknown:
            raise ValueError(f"Unknown task dependencies: {', '.join(unknown)}")
        resolved: set[str] = set()
        pending = dict(dependencies_by_id)
        while pending:
            ready = [identifier for identifier, dependencies in pending.items() if dependencies <= resolved]
            if not ready:
                raise ValueError("Workflow task dependencies contain a cycle.")
            for identifier in ready:
                resolved.add(identifier)
                pending.pop(identifier)

    async def execute(
        self,
        workflow_id: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def run_limited(task: Task) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled
            async with semaphore:
                await self._execute_task(task, working_directory, cancel_event)

        cancelled = False
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled
                ready = self.state.ready_tasks(workflow_id)
                if not ready:
                    break
                pending = [asyncio.create_task(run_limited(task)) for task in ready]
                try:
                    await asyncio.gather(*pending)
                except BaseException:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise
        except OperationCancelled:
            # Remembered for the refresh below: cancellation is a terminal
            # workflow outcome even when no task ever started to derive it.
            cancelled = True
            raise
        finally:
            # Derive the workflow row on EVERY exit, including cancellation:
            # OperationCancelled used to skip this refresh, so a cancelled
            # workflow stayed `pending` forever (its tasks were already
            # terminal, so `recover` could not repair it either) and the CLI
            # and GUI kept reporting a stopped operation as active. The
            # derivation only reads persisted task state -- a cancelled run
            # carries a FAILED task, so the row becomes FAILED, never success;
            # the cancelled flag covers cancellation before the first claim,
            # where every task is still pending and nothing derives failure.
            self.state.refresh_workflow_status(workflow_id, cancelled=cancelled)

    def _historical_performance(self, workflow_id: str) -> dict[str, float]:
        """Return deterministic recent success rates by agent identifier."""
        try:
            runs = self.state.list_agent_runs(workflow_id, limit=200)
        except Exception:
            return {}
        totals: dict[str, int] = {}
        successes: dict[str, int] = {}
        try:
            for run in runs:
                if not run.agent:
                    continue
                totals[run.agent] = totals.get(run.agent, 0) + 1
                if run.status is AgentRunStatus.COMPLETED and (
                    run.exit_code is None or run.exit_code == 0
                ):
                    successes[run.agent] = successes.get(run.agent, 0) + 1
        except Exception:
            return {}
        return {
            agent: successes.get(agent, 0) / total
            for agent, total in totals.items()
            if total
        }

    def _repository_characteristics(self, working_directory: object) -> dict[str, object]:
        """Bounded repository hints for the router.

        Inspection is scoring input only, so any failure degrades to `{}` and
        selection still proceeds through the preference/fallback path.
        """
        if not working_directory:
            return {}
        try:
            return self.repo_inspector.routing_flags(working_directory)
        except Exception:
            return {}

    def _select_agent(
        self,
        task: Task,
        excluded: set[str],
        working_directory: object = None,
        repository_characteristics: dict[str, object] | None = None,
    ) -> DetectedAgent | None:
        """Select through the router when available, otherwise use legacy selection.

        ``repository_characteristics`` lets async callers pass flags computed
        off the event loop; when omitted they are computed here.
        """
        router = getattr(self, "router", None)
        if router is not None:
            try:
                profiles = self.registry.profiles()
            except Exception:
                profiles = None
            if isinstance(profiles, dict):
                try:
                    decision = router.route(
                        role=task.role,
                        task_description=task.description,
                        repository_characteristics=(
                            repository_characteristics
                            if repository_characteristics is not None
                            else self._repository_characteristics(working_directory)
                        ),
                        # No hard capability filter here: the router still scores
                        # role/task fit, while role gates, availability, explicit
                        # user preferences, and exclusions preserve the legacy path.
                        required_capabilities=(),
                        available_agents=tuple(profiles.values()),
                        user_preferences=self.config.role_preferences.get(task.role, ()),
                        historical_performance=self._historical_performance(task.workflow_id),
                        excluded=excluded,
                    )
                except Exception:
                    return self.registry.select(task.role, excluded)
                selected = getattr(decision, "selected_agent", None)
                identifier = getattr(selected, "identifier", None)
                if identifier:
                    try:
                        agent = self.registry.get(identifier)
                        self._record_routing_decision(task, decision, agent.config.name)
                        return agent
                    except (KeyError, TypeError):
                        # A stale router/registry mapping must not silently
                        # execute a different agent than the persisted decision.
                        fallback = self.registry.select(task.role, excluded)
                        executed = fallback.config.name if fallback is not None else None
                        self._record_routing_decision(task, decision, executed)
                        return fallback
                self._record_routing_decision(task, decision, None)
                return None
        return self.registry.select(task.role, excluded)

    def _record_routing_decision(self, task: Task, decision, executed_agent: str | None = None) -> None:
        """Persist an explainable routing decision without leaking metadata."""
        try:
            payload = decision.to_dict()
            selected_profile = payload.get("selected_profile")
            if isinstance(selected_profile, dict):
                selected_profile["metadata"] = {}
            payload["executed_agent"] = executed_agent
            selected = payload.get("selected_agent") or "no agent"
            detail = selected if executed_agent in (None, selected) else f"{selected} (executed {executed_agent})"
            self.state.record_typed_event(Event(
                workflow_id=task.workflow_id,
                task_id=task.id,
                type=EventType.ROUTING_DECISION,
                message=f"Routed {task.role} to {detail}",
                payload=payload,
            ))
        except Exception as error:
            # A8 SAFE_TO_DEGRADE: selection already happened and the run must
            # proceed. The explainability record is reported as degraded.
            self.degradation.record(
                "event.routing_decision", error,
                workflow_id=task.workflow_id, task_id=task.id,
            )

    async def _execute_task(
        self,
        task: Task,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled
        claimed = self.state.claim_task(task.id)
        if claimed is None:
            # Another worker claimed this task concurrently; running it again
            # would duplicate work and corrupt task history.
            return
        task = claimed
        started = True
        try:
            if task.role == "verification":
                if self.verification_kernel is not None:
                    report = await self.verification_kernel.run_verification(
                        task.workflow_id, task.id, working_directory,
                        source_agent_run_id=self._verification_source_run(task),
                        cancel_event=cancel_event,
                    )
                    task.verification_run_id = report.run_id
                    # A1: the report must be self-consistent before it can
                    # verify anything (fail-loud: violations propagate via
                    # the StateTransitionError re-raise below, never coerce).
                    assert_report_consistent(report)
                    # An agent claiming success can never set this flag: only a
                    # passed VerificationReport marks a task verified.
                    task.verified = report.overall_status is VerificationReportStatus.PASSED
                    task.result = report.transcript
                    if task.verified:
                        task.status = TaskStatus.PASSED
                    elif report.overall_status is VerificationReportStatus.UNVERIFIED:
                        # The procedure ran but established nothing -- e.g. every
                        # configured check was inapplicable to this working tree.
                        # There is no demonstrated defect, so BLOCKED, not FAILED:
                        # FAILED would send the workflow into repair logic for a
                        # defect no check actually found.
                        task.status = TaskStatus.BLOCKED
                    else:
                        task.status = TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        self._record_verification_failure(task, report)
                else:
                    results = await self.verifier.run(working_directory, cancel_event)
                    # A1: an empty command suite is vacuous success and must
                    # not verify (supersedes Review #2 — see decisions.md).
                    succeeded = bool(results) and all(result.succeeded for result in results)
                    # Verification output is persisted evidence, so it follows
                    # the same redaction policy as agent output.
                    task.result = redact_text("\n".join(result.output for result in results))
                    # Legacy verification commands are verification evidence
                    # themselves; only agent-task success leaves verified False.
                    task.verified = succeeded
                    task.status = TaskStatus.PASSED if succeeded else TaskStatus.FAILED
                    if task.status is TaskStatus.FAILED:
                        self._record_legacy_verification_failure(task, results)
            else:
                if _is_security_role(task.role):
                    prescan_reason = self._security_prescan_reason(task, working_directory)
                    if prescan_reason is not None:
                        task.status = TaskStatus.FAILED
                        task.result = prescan_reason
                        self._record_review_verdict(task, None)
                        self.record_failure(
                            task,
                            FailureClassifier.classify(
                                source=FailureSource.REVIEW, error=prescan_reason,
                                role=task.role,
                            ),
                            evidence=prescan_reason, primary_error=prescan_reason,
                        )
                        task.finished_at = utc_now()
                        self.state.update_task(task)
                        return
                excluded = {task.assigned_agent} if task.assigned_agent and task.attempts > 1 else set()
                repository_characteristics = None
                if getattr(self, "router", None) is not None:
                    # The bounded filesystem walk runs in a worker thread so a
                    # slow or hostile tree cannot block the event loop.
                    # Inspection is scoring input only: a failure here (an
                    # override that raises, a shut-down executor) degrades to
                    # empty hints so selection still routes, as it did when
                    # the call sat inside _select_agent's router guard.
                    try:
                        repository_characteristics = await asyncio.to_thread(
                            self._repository_characteristics, working_directory,
                        )
                    except Exception:
                        repository_characteristics = {}
                agent = self._select_agent(
                    task, excluded, working_directory, repository_characteristics,
                )
                if agent is None:
                    task.status = TaskStatus.FAILED
                    if excluded:
                        task.result = (
                            f"No installed agent supports role '{task.role}' "
                            f"(already tried and excluded: {', '.join(sorted(excluded))})."
                        )
                        self.state.event(task.workflow_id, task.id, "no-agent-fallback", task.result)
                    else:
                        task.result = f"No installed agent supports role '{task.role}'."
                    self._record_no_agent_run(task, working_directory)
                    self.record_failure(
                        task,
                        FailureClassifier.classify(
                            source=FailureSource.ENVIRONMENT, error=task.result,
                            role=task.role, agent_available=False,
                        ),
                        evidence=task.result, primary_error=task.result,
                    )
                else:
                    task.assigned_agent = agent.config.name
                    result = await self._run_task_agent(
                        agent, task, working_directory, cancel_event=cancel_event
                    )
                    # Redact before persisting: the tasks table is a durable
                    # boundary and agent output can carry credential-like
                    # material. The log path still points at the full
                    # transcript for operators who need it.
                    task.result = (
                        f"log={result.log_path}\n"
                        f"{redact_text(result.stdout or '')}\n"
                        f"{redact_text(result.stderr or '')}"
                    ).strip()
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled
                    # exit 0 is evidence the PROCESS ran, not that the WORK was
                    # done. Check the role's evidence contract before crediting
                    # success: an agent that exited cleanly having changed nothing
                    # must not PASS a role whose job is to change code.
                    # Likewise an exit-0 run whose structured result explicitly
                    # says failure/partial did not do the work: exit_code == 0
                    # must never override the agent's own failure signal.
                    satisfied, reason = self._evidence_satisfied(task, result, working_directory)
                    agent_status = _structured_agent_status(result)
                    # Only an explicit SUCCESS (or no structured payload at all,
                    # the legacy plaintext path) counts as agent success.
                    # FAILURE, PARTIAL, and UNKNOWN must never be promoted.
                    structured_ok = agent_status in (None, AgentResultStatus.SUCCESS)
                    success_denied = _explicit_success_denied(result)
                    verdict_ok = True
                    verdict_reason = ""
                    verdict = None
                    if _requires_review_verdict(task.role):
                        verdict = parse_verdict(result.stdout)
                        verdict_ok = verdict is not None and verdict.is_approved
                        if verdict is None:
                            verdict_reason = (
                                "review verdict missing or malformed; expected the final output line "
                                "to contain a valid structured verdict"
                            )
                        elif not verdict.is_approved:
                            if verdict.blocking:
                                findings = redact_text("; ".join(verdict.blocking))[:1000]
                                verdict_reason = f"review verdict rejected the change; blocking findings: {findings}"
                            else:
                                verdict_reason = "review verdict rejected the change"
                    self._record_review_verdict(task, verdict)
                    task.status = TaskStatus.PASSED if (
                        result.succeeded and satisfied and structured_ok
                        and not success_denied and verdict_ok
                    ) else TaskStatus.FAILED
                    if not satisfied and result.succeeded:
                        task.result = f"{task.result}\n\n{reason}".strip()
                    if not structured_ok and result.succeeded and satisfied:
                        task.result = (
                            f"{task.result}\n\nagent_status={agent_status.value}: the agent exited "
                            "cleanly but its structured result reports "
                            f"{agent_status.value}, so the task did not pass"
                        ).strip()
                    if success_denied and result.succeeded and satisfied and structured_ok:
                        task.result = (
                            f"{task.result}\n\nthe agent exited cleanly but its structured "
                            "output explicitly reports success=false, so the task did not pass"
                        ).strip()
                    if verdict_reason:
                        task.result = f"{task.result}\n\n{verdict_reason}".strip()
                    if task.status is TaskStatus.FAILED:
                        self._record_agent_failure(task, result)
        except StateTransitionError:
            # A1: invariant violations are programmer errors — never fold
            # them into task failure records; abort loudly so the suite
            # (or the operator) sees the real bug.
            raise
        except asyncio.CancelledError:
            if started:
                task.status = TaskStatus.FAILED
                task.result = "Task was cancelled."
                task.finished_at = utc_now()
                try:
                    self.record_failure(
                        task,
                        FailureClassifier.classify(source=FailureSource.SYSTEM, cancelled=True),
                        evidence=task.result, primary_error="Task was cancelled.",
                    )
                except Exception as persist_error:
                    # A8: a lost failure row must stay visible even while
                    # the cancellation outcome itself is being recorded.
                    self.degradation.record(
                        "failure.create", persist_error,
                        workflow_id=task.workflow_id, task_id=task.id,
                    )
                self.state.update_task(task)
            raise
        except OperationCancelled:
            if started:
                task.status = TaskStatus.FAILED
                task.result = "Task was cancelled."
                task.finished_at = utc_now()
                try:
                    self.record_failure(
                        task,
                        FailureClassifier.classify(source=FailureSource.SYSTEM, cancelled=True),
                        evidence=task.result, primary_error="Task was cancelled.",
                    )
                except Exception as persist_error:
                    # A8: a lost failure row must stay visible even while
                    # the cancellation outcome itself is being recorded.
                    self.degradation.record(
                        "failure.create", persist_error,
                        workflow_id=task.workflow_id, task_id=task.id,
                    )
                self.state.update_task(task)
            raise
        except Exception as error:
            task.status = TaskStatus.FAILED
            # An exception message can embed command output or a credential.
            task.result = redact_text(f"Task execution error: {error}")
            try:
                self.record_failure(
                    task,
                    FailureClassifier.classify(source=FailureSource.SYSTEM, error=str(error)),
                    evidence=task.result, primary_error=str(error),
                )
            except Exception as persist_error:
                self.degradation.record(
                    "failure.create", persist_error,
                    workflow_id=task.workflow_id, task_id=task.id,
                )
        if task.status is TaskStatus.FAILED and task.attempts < task.max_attempts:
            # Bounded retry: consult the deterministic policy (attempt budget
            # enforced by max_attempts) and back off, respecting cancellation.
            # Every retry creates a distinct AgentRun linked to its parent via
            # _agent_parent (RETRY/REPAIR); inherited context is injected via
            # _prompt_with_history on the next attempt.
            try:
                await self.retry_policy.asleep(task.attempts + 1)
            except asyncio.CancelledError:
                task.finished_at = utc_now()
                self.state.update_task(task)
                raise
            if cancel_event is not None and cancel_event.is_set():
                task.finished_at = utc_now()
                self.state.update_task(task)
                raise OperationCancelled
            task.status = TaskStatus.PENDING
            task.result = f"Attempt {task.attempts} failed; retrying.\n{task.result}"
        if task.status in {TaskStatus.PASSED, TaskStatus.FAILED}:
            task.finished_at = utc_now()
        if task.status is TaskStatus.PASSED:
            # A1: completion rules checked outside the try above so a
            # violation propagates instead of becoming a task failure.
            assert_task_completion(task)
        self.state.update_task(task)

    def _evidence_satisfied(self, task: Task, result, working_directory) -> tuple[bool, str]:
        """Apply the role's evidence contract to a successful agent run.

        Measures the working tree directly instead of trusting the persisted
        AgentRunOutcome. Two reasons: `RunResult` does not carry `files_changed`
        at all, and a runner double that accepts but ignores the metadata
        collector still produces a row whose empty `files_changed` means "not
        measured" rather than "nothing changed". Measuring here makes the check
        independent of who ran the agent.
        """
        requirement = expected_evidence(task.role)
        if requirement is not TaskEvidence.WORKTREE_CHANGE:
            return True, ""
        try:
            collector = self.metadata_collector or GitRunMetadataCollector()
            metadata = collector(working_directory)
        except Exception:
            # Could not measure: report satisfied and let verification decide,
            # rather than failing work on a measurement failure.
            return True, ""
        if not _is_git_working_tree(working_directory):
            # The collector reads `git status`, so outside a repository it
            # reports nothing at all. That is "cannot observe", not "observed
            # empty" -- conflating the two would fail every workflow run
            # against a plain directory, which several tests and some real
            # invocations do.
            return True, ""
        if metadata.files_changed:
            return True, ""
        return False, (
            "no_evidence: the agent exited cleanly but changed no files in "
            f"{working_directory}, so it did not do the work it was asked to do"
        )

    def _record_agent_failure(self, task: Task, result) -> None:
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else None
        except Exception:
            run_id = None
        stderr = getattr(result, "stderr", "") or ""
        # Agent commands embed the raw prompt, which may carry secrets: only
        # the executable is persisted as structured evidence.  Verification
        # check commands are allowlisted config and keep their full argv.
        raw_command = tuple(getattr(result, "command", ()) or ())
        evidence = FailureEvidence(
            source=FailureSource.AGENT,
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            cancelled=bool(getattr(result, "cancelled", False)),
            terminated=bool(getattr(result, "terminated", False)),
            command=raw_command[:1],
            stderr_peek=redact_text(stderr)[-500:] or None,
        )
        classification = FailureClassifier.classify(
            source=FailureSource.AGENT,
            exit_code=getattr(result, "exit_code", None),
            timed_out=bool(getattr(result, "timed_out", False)),
            cancelled=bool(getattr(result, "cancelled", False)),
            terminated=bool(getattr(result, "terminated", False)),
            error=task.result,
            role=task.role,
            evidence=evidence,
        )
        try:
            self.record_failure(
                task, classification, agent_run_id=run_id,
                verification_run_id=task.verification_run_id,
                evidence=task.result, primary_error=task.result,
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create", persist_error,
                workflow_id=task.workflow_id, task_id=task.id,
            )

    def _record_legacy_verification_failure(self, task: Task, results) -> None:
        """Record a failed legacy verification run with structured evidence.

        Legacy commands have no check class, so the evidence is persisted for
        inspection while the legacy text rules keep deciding the category.
        """
        failing = results[-1] if results else None
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION,
            exit_code=None if failing is None or failing.timed_out else failing.exit_code,
            timed_out=bool(failing.timed_out) if failing is not None else False,
            command=tuple(failing.command) if failing is not None else (),
        )
        classification = FailureClassifier.classify(
            source=FailureSource.VERIFICATION,
            error=task.result, role=task.role,
            evidence=evidence,
        )
        try:
            self.record_failure(
                task, classification,
                verification_run_id=task.verification_run_id,
                evidence=task.result, primary_error="Legacy verification commands failed.",
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create", persist_error,
                workflow_id=task.workflow_id, task_id=task.id,
            )

    @staticmethod
    def _check_attribute(check: object, name: str) -> object:
        try:
            return getattr(check, name, None)
        except Exception:
            return None

    def _record_verification_failure(self, task: Task, report) -> None:
        failing = [
            check for check in getattr(report, "checks", ()) or ()
            if self._check_attribute(check, "status") is None
            or getattr(self._check_attribute(check, "status"), "value", None) != "passed"
        ]
        first = failing[0] if failing else None
        raw_class = self._check_attribute(first, "check_class")
        if isinstance(raw_class, str):
            check_class = raw_class
        else:
            try:
                check_class = raw_class.value if raw_class is not None else None
            except Exception:
                check_class = None
        try:
            run = self.state.latest_agent_run(task.workflow_id, task.id)
            run_id = run.id if run is not None else self._verification_source_run(task)
        except Exception:
            run_id = self._verification_source_run(task)
        raw_command = self._check_attribute(first, "command") or ()
        try:
            command = tuple(raw_command)
        except Exception:
            command = ()
        evidence = FailureEvidence(
            source=FailureSource.VERIFICATION,
            check_class=check_class,
            exit_code=self._check_attribute(first, "exit_code"),
            command=command,
        )
        classification = FailureClassifier.classify(
            source=FailureSource.VERIFICATION,
            error=task.result, check_class=check_class, role=task.role,
            evidence=evidence,
        )
        try:
            verification_run_id = self._check_attribute(report, "run_id")
            self.record_failure(
                task, classification, agent_run_id=run_id,
                verification_run_id=verification_run_id if isinstance(verification_run_id, str) else None,
                evidence=task.result, primary_error="Verification report did not pass.",
                structured_evidence=evidence,
            )
        except Exception as persist_error:
            self.degradation.record(
                "failure.create", persist_error,
                workflow_id=task.workflow_id, task_id=task.id,
            )

    def _verification_source_run(self, task: Task) -> str | None:
        candidates = []
        for dependency in task.dependencies:
            try:
                run = self.state.latest_agent_run(task.workflow_id, dependency)
            except Exception:
                continue
            if run is not None:
                candidates.append(run)
        if not candidates:
            return None
        return max(candidates, key=lambda run: (run.created_at, run.id)).id

    def verification_evidence(self, workflow_id: str) -> list[Task]:
        """Return verification tasks backed by passed verification evidence.

        Kernel path: verified flag plus a linked VerificationReport run.
        Legacy path: verified flag plus the command-output transcript (the
        output IS the evidence — no run object exists there).  Single
        definition used by READY gating everywhere (Track A1).
        """
        return [
            task for task in self.state.list_tasks(workflow_id)
            if task.role == "verification" and task.verified and (
                task.verification_run_id or (task.result or "").strip()
            )
        ]

    def workflow_readiness(
        self,
        workflow_id: str,
        *,
        verification_task_id: str | None = None,
        review_task_id: str | None = None,
    ) -> WorkflowReadiness:
        """Assess the authoritative READY contract for one workflow.

        Every entry point that can declare a workflow READY — the CLI custom-DAG
        path, the GUI, and :meth:`run_high_level` — assesses readiness through
        this method, so no path can hold a weaker contract than the standard
        flow.
        """
        return assess_workflow_readiness(
            self.state.list_tasks(workflow_id), workflow_id,
            verification_task_id=verification_task_id,
            review_task_id=review_task_id,
        )

    def _run_observer_for_task(self) -> AgentRunObserver | None:
        if self.run_observer is not None:
            return self.run_observer
        factory = getattr(self.state, "agent_run_observer", None)
        if callable(factory):
            try:
                observer = factory()
            except Exception:
                return None
            return observer if hasattr(observer, "create_run") else None
        return None

    def _agent_parent(self, task: Task) -> tuple[AgentRunRelationship, str | None]:
        try:
            if task.role == "debugging":
                candidates = []
                for dependency in task.dependencies:
                    dependency_run = self.state.latest_agent_run(task.workflow_id, dependency)
                    if dependency_run is not None:
                        candidates.append(dependency_run)
                if candidates:
                    parent = max(candidates, key=lambda run: (run.created_at, run.id))
                    return AgentRunRelationship.REPAIR, parent.id
                parent = self.state.latest_agent_run(task.workflow_id, statuses=(AgentRunStatus.FAILED,))
                return AgentRunRelationship.REPAIR, parent.id if parent is not None else None
            if task.attempts > 1:
                parent = self.state.latest_agent_run(task.workflow_id, task.id)
                if parent is not None:
                    return AgentRunRelationship.RETRY, parent.id
        except Exception:
            return AgentRunRelationship.ROOT, None
        return AgentRunRelationship.ROOT, None

    def _agent_run_context(
        self,
        task: Task,
        prompt: str,
        working_directory: str | Path,
        agent: object | None = None,
    ) -> AgentRunContext:
        relationship, parent_run_id = self._agent_parent(task)
        resolved = str(Path(working_directory).resolve())
        if agent is None:
            return AgentRunContext(
                workflow_id=task.workflow_id,
                task_id=task.id,
                attempt=task.attempts,
                role=task.role,
                working_directory=resolved,
                worktree=resolved,
                prompt=prompt,
                parent_run_id=parent_run_id,
                relationship=relationship,
            )
        return AgentRunContext(
            agent=agent.config.name,
            executable=agent.executable or agent.config.command,
            role=task.role,
            model=getattr(agent.config, "model", None),
            workflow_id=task.workflow_id,
            task_id=task.id,
            attempt=task.attempts,
            working_directory=resolved,
            worktree=resolved,
            prompt=prompt,
            command=self.runner.build_command(agent, prompt),
            parent_run_id=parent_run_id,
            relationship=relationship,
        )

    def _note_run_degradation(
        self,
        error: BaseException,
        *,
        run_id: str | None = None,
        task: Task | None = None,
        context: AgentRunContext | None = None,
    ) -> None:
        """A8: a post-hoc run record that could not be stored is a lost run.

        The runner's own observer notifications are covered in ``runner.py``;
        these are the engine-side fallbacks used when the runner did not create
        the run itself.  Without them the loss was completely silent.
        """
        self.degradation.record(
            "agent_run.create" if run_id is None else "agent_run.transition",
            error,
            run_id=run_id,
            workflow_id=getattr(context, "workflow_id", None) or getattr(task, "workflow_id", None),
            task_id=getattr(context, "task_id", None) or getattr(task, "id", None),
        )

    def _record_no_agent_run(self, task: Task, working_directory: str | Path) -> None:
        observer = self._run_observer_for_task()
        if observer is None:
            return
        context = self._agent_run_context(task, "", working_directory)
        run_id: str | None = None
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.mark_starting(run_id)
            observer.mark_running(run_id)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=AgentRunStatus.FAILED,
                    error=task.result,
                    failure_classification="no_agent",
                ),
            )
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, task=task, context=context)

    def _record_completed_run(
        self,
        observer: AgentRunObserver,
        context: AgentRunContext,
        result: RunResult,
        working_directory: str | Path,
    ) -> None:
        run_id: str | None = None
        try:
            collector = self.metadata_collector or GitRunMetadataCollector()
            try:
                metadata = collector(working_directory)
            except Exception:
                metadata = AgentRunMetadata()
            status = (
                AgentRunStatus.TIMED_OUT if result.timed_out else
                AgentRunStatus.COMPLETED if result.succeeded else
                AgentRunStatus.FAILED
            )
            stdout = getattr(result, "stdout", "")
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=status,
                    exit_code=result.exit_code,
                    duration_seconds=getattr(result, "duration_seconds", None),
                    cancelled=bool(getattr(result, "cancelled", False)),
                    timed_out=result.timed_out,
                    terminated=bool(getattr(result, "terminated", False)),
                    stdout_path=str(getattr(result, "stdout_path", result.log_path)),
                    stderr_path=str(getattr(result, "stderr_path", result.log_path)),
                    log_path=str(result.log_path),
                    files_changed=metadata.files_changed,
                    diff_stat=metadata.diff_stat,
                    failure_classification=(
                        None if status is AgentRunStatus.COMPLETED else
                        "timeout" if status is AgentRunStatus.TIMED_OUT else
                        "nonzero_exit"
                    ),
                    structured_result=getattr(result, "structured_result", None)
                    or _safe_workflow_structured(stdout),
                ),
            )
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, context=context)

    def _record_cancelled_run(
        self, observer: AgentRunObserver, context: AgentRunContext, duration: float | None
    ) -> None:
        run_id: str | None = None
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.finish_run(
                run_id,
                AgentRunOutcome(
                    status=AgentRunStatus.CANCELLED,
                    duration_seconds=duration,
                    cancelled=True,
                    failure_classification="cancelled",
                ),
            )
        except Exception as error:
            self._note_run_degradation(error, run_id=run_id, context=context)

    def _record_failed_run(
        self, observer: AgentRunObserver, context: AgentRunContext, error: BaseException, classification: str
    ) -> None:
        run_id: str | None = None
        try:
            created = observer.create_run(context)
            run_id = created.id if isinstance(created, AgentRun) else str(created)
            observer.fail_run(run_id, error, classification)
        except Exception as persist_error:
            self._note_run_degradation(persist_error, run_id=run_id, context=context)

    def _supported_runner_kwargs(self) -> dict[str, object]:
        try:
            parameters = inspect.signature(self.runner.run_agent).parameters
        except (TypeError, ValueError):
            return {}
        accepts_keywords = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        supported: dict[str, object] = {}
        for name in ("run_context", "run_observer", "metadata_collector"):
            if name in parameters or accepts_keywords:
                supported[name] = True
        return supported

    async def _run_task_agent(
        self,
        agent: object,
        task: Task,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        observer = self._run_observer_for_task()
        try:
            prompt = self._prompt_with_history(task, working_directory)
        except Exception:
            prompt = self._prompt(task, working_directory)
        context = self._agent_run_context(task, prompt, working_directory, agent)
        supported = self._supported_runner_kwargs()
        collector = self.metadata_collector or GitRunMetadataCollector()
        call_kwargs: dict[str, object] = {}
        if "run_context" in supported:
            call_kwargs["run_context"] = context
        if "run_observer" in supported and observer is not None:
            call_kwargs["run_observer"] = observer
        if "metadata_collector" in supported:
            call_kwargs["metadata_collector"] = collector
        started = monotonic()
        if "run_observer" in supported and observer is not None:
            # A runner that understands the observer normally owns the
            # complete pending -> running -> terminal sequence.  The recorder
            # preserves that behavior while retaining a fallback for runner
            # doubles that accept, but ignore, observer arguments.
            recorder = _RecordingObserver(observer)
            call_kwargs["run_observer"] = recorder
            try:
                result = await self.runner.run_agent(
                    agent, prompt, working_directory, task.id,
                    cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
                )
            except OperationCancelled:
                if recorder.created_run_id is None:
                    self._record_cancelled_run(observer, context, monotonic() - started)
                raise
            except Exception as error:
                if recorder.created_run_id is None:
                    self._record_failed_run(observer, context, error, "process_error")
                raise
            if recorder.created_run_id is not None:
                return result
            self._record_completed_run(observer, context, result, working_directory)
            return result
        if "run_observer" in supported:
            return await self.runner.run_agent(
                agent, prompt, working_directory, task.id,
                cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
            )
        run_id: str | None = None
        started = monotonic()
        if observer is not None:
            try:
                created = observer.create_run(context)
                run_id = created.id if isinstance(created, AgentRun) else str(created)
                observer.mark_starting(run_id)
                observer.mark_running(run_id)
            except Exception as error:
                self._note_run_degradation(
                    error, run_id=run_id, context=context)
                run_id = None
        try:
            result = await self.runner.run_agent(
                agent, prompt, working_directory, task.id,
                cancel_event=cancel_event, **call_kwargs,  # type: ignore[arg-type]
            )
        except OperationCancelled:
            if observer is not None and run_id is not None:
                try:
                    observer.finish_run(
                        run_id,
                        AgentRunOutcome(
                            status=AgentRunStatus.CANCELLED,
                            duration_seconds=monotonic() - started,
                            cancelled=True,
                            failure_classification="cancelled",
                        ),
                    )
                except Exception as error:
                    self._note_run_degradation(
                        error, run_id=run_id, context=context)
            raise
        except Exception as error:
            if observer is not None and run_id is not None:
                try:
                    observer.fail_run(run_id, error, "process_error")
                except Exception as persist_error:
                    self._note_run_degradation(
                        persist_error, run_id=run_id, context=context)
            raise
        if observer is not None and run_id is not None:
            try:
                metadata = AgentRunMetadata()
                try:
                    metadata = collector(working_directory)
                except Exception:
                    pass
                status = (
                    AgentRunStatus.TIMED_OUT if result.timed_out else
                    AgentRunStatus.COMPLETED if result.succeeded else
                    AgentRunStatus.FAILED
                )
                observer.finish_run(
                    run_id,
                    AgentRunOutcome(
                        status=status,
                        exit_code=result.exit_code,
                        duration_seconds=result.duration_seconds,
                        cancelled=bool(getattr(result, "cancelled", False)),
                        timed_out=result.timed_out,
                        terminated=bool(getattr(result, "terminated", False)),
                        stdout_path=str(getattr(result, "stdout_path", result.log_path)),
                        stderr_path=str(getattr(result, "stderr_path", result.log_path)),
                        log_path=str(result.log_path),
                        files_changed=metadata.files_changed,
                        diff_stat=metadata.diff_stat,
                        failure_classification=None if status is AgentRunStatus.COMPLETED else "nonzero_exit",
                        structured_result=getattr(result, "structured_result", None)
                        or _safe_workflow_structured(result.stdout),
                    ),
                )
            except Exception as persist_error:
                self._note_run_degradation(
                    persist_error, run_id=run_id, context=context)
        return result

    def _dependency_handoff(self, task: Task) -> str:
        """Bounded prompt context from this task's completed dependencies.

        A dependent agent is only useful if it can see what the task it depends
        on produced: without this, `implementation` never reads the architecture
        plan and `review` never inspects the change. Deterministic by
        construction -- dependency order, fixed truncation, no LLM. Reads the
        already-redacted `task.result`; prompts are never persisted.
        """
        if not task.dependencies:
            return ""
        try:
            tasks = {item.id: item for item in self.state.list_tasks(task.workflow_id)}
        except Exception:
            return ""
        blocks: list[str] = []
        budget = HANDOFF_TOTAL_CHARS
        for dependency_id in task.dependencies:
            dependency = tasks.get(dependency_id)
            if dependency is None or dependency.id == task.id:
                continue
            # Only a PASSED dependency has a result worth passing on. The
            # workflow schedules a task once its dependencies pass, so this
            # covers a dependency reached through a retry.
            if dependency.status is not TaskStatus.PASSED:
                continue
            result = (dependency.result or "").strip()
            if not result:
                continue
            if len(result) > HANDOFF_RESULT_CHARS:
                result = result[:HANDOFF_RESULT_CHARS].rstrip() + "\n[truncated]"
            block = (
                f"Previous task:\nRole: {dependency.role}\n\n"
                f"Description:\n{dependency.description}\n\n"
                f"Result:\n{result}\n"
            )
            if len(block) > budget:
                break
            blocks.append(block)
            budget -= len(block)
        if not blocks:
            return ""
        return "Completed dependency work you can rely on:\n" + "\n".join(blocks)

    def _prompt_with_history(self, task: Task, working_directory: str | Path) -> str:
        base = self._prompt(task, working_directory)
        if task.attempts <= 1:
            return base
        try:
            failures = self.state.list_failures(task.workflow_id, task.id, limit=1)
        except Exception:
            return base
        if not failures:
            return base
        try:
            return build_retry_prompt(base, failures[0])
        except Exception:
            return base

    def _prompt(self, task: Task, working_directory: str | Path) -> str:
        # The fallback path in `_run_task_agent` carries the same dependency
        # context, so a prompt does not depend on whether history read worked.
        common = (
            f"You are the {task.role} agent for AgentOps task {task.id}.\n"
            f"Workspace: {Path(working_directory).resolve()}\n"
            f"Request: {task.description}\n"
            "Work only in this workspace. Do not read secrets or alter AgentOps configuration. "
        )
        if _is_security_role(task.role):
            try:
                findings, completed = _local_security_scan(working_directory)
                scan_report = (
                    findings_for_prompt(findings) if completed
                    else "Local secret scan: unavailable (the scan did not "
                         "complete; no prescan findings either way)."
                )
            except Exception:
                scan_report = "Local secret scan: unavailable."
            base = common + (
                "Security-review the completed change. Inspect each item and "
                "report concrete file:line evidence for anything suspicious: "
                "(1) secrets or credentials in the diff; "
                "(2) authentication and permission changes; "
                "(3) dependency, manifest, and lockfile changes; "
                "(4) newly added shell commands or network calls; "
                "(5) files changed outside the task's declared scope. "
                f"{scan_report} "
                "Never quote a suspected secret value in your output -- name the file and pattern only. "
                "Summarize your findings first. Your FINAL output line "
                "MUST be one valid JSON object using this schema: "
                '{"schema_version": 1, "verdict": "approve", "blocking": [], '
                '"non_blocking": [], "confidence": 0.9}. '
                "Use verdict=\"reject\" if any blocking issue remains or you cannot approve safely. "
                "Both finding fields must be arrays of strings; include every field. Approve only with "
                "an empty blocking array. Do not put any other JSON object after the verdict, and do not "
                "write anything after its final line."
            )
        elif _requires_review_verdict(task.role):
            base = common + (
                "Review the completed work and summarize your findings first. Your FINAL output line "
                "MUST be one valid JSON object using this schema: "
                '{"schema_version": 1, "verdict": "approve", "blocking": [], '
                '"non_blocking": [], "confidence": 0.9}. '
                "Use verdict=\"reject\" if any blocking issue remains or you cannot approve safely. "
                "Both finding fields must be arrays of strings; include every field. Approve only with "
                "an empty blocking array. Do not put any other JSON object after the verdict, and do not "
                "write anything after its final line."
            )
        else:
            base = common + (
                "Summarize changes and tests in your final response. "
                "Your FINAL output line MUST be one valid JSON AgentResult "
                "object using exactly this schema: "
                '{"schema_version": 1, "status": "success|failure|partial", '
                '"summary": "...", "files_changed": [], "tests_run": 0, '
                '"tests_passed": 0, "tests_failed": 0}. '
                "Use status=failure when the work could not be completed, "
                "with a plain-language summary of what happened. "
                "Do not write anything after that final JSON line."
            )
        scope = _role_scope_text(task.role, working_directory)
        if scope:
            base = f"{base}\n\nRole scope:\n{scope}"
        handoff = self._dependency_handoff(task)
        return f"{base}\n\n{handoff}" if handoff else base

    def _review_failure_context(self, review: Task) -> str:
        """Actionable text describing why the review rejected the change.

        `task.result` alone is not enough: a review that exhausts its attempts
        ends holding the LAST attempt's message. Observed live -- the review
        rejected the change, the retry found no alternative agent, and `result`
        became "No installed agent supports role 'review'". The finding survived
        only on the review task's failure rows, and repairing from `result` sent
        the debugging agent after a nonexistent routing bug.
        """
        parts: list[str] = []
        seen: set[str] = set()

        def add(text: str | None) -> None:
            value = (text or "").strip()
            if value and value not in seen:
                seen.add(value)
                parts.append(value)

        try:
            failures = self.state.list_failures(review.workflow_id, review.id, limit=20)
        except Exception:
            failures = []
        for failure in failures:
            add(failure.primary_error or failure.evidence)
        add(review.result)
        # The parsed blocking findings are the actionable repair context:
        # they name what the reviewer wants changed, while raw transcripts
        # bury it. They lead so a repair agent reads them first.
        try:
            blocking = (verdict_summary_for_task(review) or {}).get("blocking") or []
            if blocking:
                findings = "\n".join(f"- {redact_text(str(item))[:500]}" for item in blocking)
                parts.insert(0, f"Blocking review findings:\n{findings}")
        except Exception:
            pass
        context = "\n\n".join(parts)
        if len(context) > HANDOFF_RESULT_CHARS:
            context = context[:HANDOFF_RESULT_CHARS].rstrip() + "\n[truncated]"
        return context

    def _record_review_verdict(self, task: Task, verdict: object | None) -> None:
        """Persist the review/security verdict as a redacted timeline event."""
        try:
            if not _requires_review_verdict(task.role):
                return
            if verdict is None:
                payload: dict[str, object] = {"verdict": "missing", "approved": False,
                                             "blocking": [], "non_blocking": []}
                severity = EventSeverity.WARNING
            else:
                payload = {"verdict": verdict.verdict, "approved": verdict.is_approved,
                           "blocking": [redact_text(item)[:500] for item in verdict.blocking],
                           "non_blocking": [redact_text(item)[:500] for item in verdict.non_blocking]}
                severity = EventSeverity.INFO if verdict.is_approved else EventSeverity.WARNING
            self.state.record_typed_event(Event(
                workflow_id=task.workflow_id, task_id=task.id,
                type=EventType.REVIEW_COMPLETED, severity=severity,
                message=f"{task.role} verdict: {payload['verdict']}",
                payload=_scrub_structured(payload),
            ))
        except Exception as error:
            self.degradation.record(
                "event.review_verdict", error,
                workflow_id=getattr(task, "workflow_id", None),
                task_id=getattr(task, "id", None),
            )

    def _security_prescan_reason(
        self, task: Task, working_directory: str | Path,
    ) -> str | None:
        """Fail-closed secret screen for the security role.

        Returns a redacted failure reason when the local scan finds
        credential-like patterns in the worktree diff, else ``None``. A
        positive local scan is demonstrated evidence -- like a failed
        verification -- so the task fails without invoking an agent: no
        agent verdict can waive a secret sitting in the diff. A scan that
        could not run is never silent: the degradation is recorded as a
        WARNING timeline event, because "no findings" must not be readable
        as "nothing to find". Never raises.
        """
        findings: list = []
        completed = False
        try:
            findings, completed = _local_security_scan(working_directory)
        except Exception:
            completed = False
        if not completed:
            try:
                self.state.record_typed_event(Event(
                    workflow_id=task.workflow_id, task_id=task.id,
                    type=EventType.NOTE, severity=EventSeverity.WARNING,
                    message="local secret prescan did not complete; the "
                            "security agent reviews without prescan findings",
                    payload=_scrub_structured({"role": task.role,
                                               "reason": "prescan_unavailable"}),
                ))
            except Exception:
                pass
        if not findings:
            return None
        try:
            lines = [
                "security policy: the local secret scan found "
                f"{len(findings)} credential-like pattern(s) in the change; "
                "remove them (or prove them inert) before this can merge.",
            ]
            for finding in findings[:10]:
                lines.append(f"- {finding.describe()}")
            if len(findings) > 10:
                lines.append(f"({len(findings) - 10} more omitted)")
            # Findings carry pattern names and locations only, never values;
            # redact defensively before persisting.
            return redact_text("\n".join(lines))
        except Exception:
            return "security policy: the local secret scan found credential-like patterns."

    async def _gate_repair_security(
        self,
        workflow_id: str,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None,
        implementation_id: str,
        review_id: str,
    ) -> Task | None:
        """Fresh security check for a repaired change, when enabled.

        The pre-repair security verdict (if any) judged the unrepaired
        change, so a repair that passes re-review gets its own security
        task rather than inheriting the old one. Returns ``None`` when
        the security gate is disabled. Never merges; the caller decides.
        """
        try:
            if not getattr(self.config, "security_review", False):
                return None
            security = self.state.add_task(Task(
                f"Security-review the repaired change for: {description}",
                "security", workflow_id,
                dependencies=(review_id, implementation_id),
                max_attempts=self.config.max_attempts))
        except Exception:
            return None
        await self.execute(workflow_id, working_directory, cancel_event)
        try:
            return self.state.get_task(security.id)
        except Exception:
            return None

    async def _repair_failed_review(
        self,
        workflow_id: str,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None,
        implementation_id: str,
        review: Task,
    ) -> WorkflowResult:
        """Repair a demonstrated review rejection, then re-verify and re-review.

        Each cycle persists three real tasks -- debugging, verification, review
        -- so the repair is part of the run's history, not an invisible retry.
        Bounded by `workflows.max_review_rounds` (default 2); the debugging
        prompt leads with the parsed blocking findings as actionable repair
        context. READY is still decided only by `assert_tasks_ready`, so an
        exhausted budget returns not READY with the last verdict preserved.
        Rejected or unreviewed changes are never merged.
        """
        if review.status is not TaskStatus.FAILED:
            # Only a FAILED review demonstrated a problem. BLOCKED means the
            # review never ran, so repairing would invent a defect -- the
            # review-side twin of the UNVERIFIED rule in `run_high_level`.
            return WorkflowResult(
                workflow_id, False,
                f"Review was {review.status.value}; no review failure was "
                f"demonstrated, so no repair was attempted.",
            )
        max_rounds = getattr(self.config, "max_review_rounds", 2)
        try:
            max_rounds = max(0, int(max_rounds))
        except (TypeError, ValueError):
            max_rounds = 2
        review_context = self._review_failure_context(review)
        for _ in range(max_rounds):
            repair = self.state.add_task(Task(
                "Repair the review findings.\n" + review_context,
                "debugging", workflow_id, dependencies=(implementation_id,),
                max_attempts=self.config.max_attempts))
            reverify = self.state.add_task(Task(
                "Re-run configured project verification commands after a review repair.",
                "verification", workflow_id, dependencies=(repair.id,), max_attempts=1))
            re_review = self.state.add_task(Task(
                f"Re-review the repaired change for: {description}",
                "review", workflow_id, dependencies=(reverify.id, implementation_id),
                max_attempts=self.config.max_attempts))
            await self.execute(workflow_id, working_directory, cancel_event)
            verification = self.state.get_task(reverify.id)
            review = self.state.get_task(re_review.id)
            review_context = self._review_failure_context(review)
            if verification.status is not TaskStatus.PASSED or not verification.verified:
                # The repair broke verification. That is a different defect than
                # the rejection this cycle was opened for, and re-sending the
                # same findings would not address it.
                return WorkflowResult(
                    workflow_id, False,
                    f"Verification was {verification.status.value} after the "
                    f"review repair; the repaired change does not verify.",
                )
            if review.status is TaskStatus.PASSED:
                if getattr(self.config, "security_review", False):
                    security = await self._gate_repair_security(
                        workflow_id, description, working_directory, cancel_event,
                        implementation_id, review.id,
                    )
                    # An enabled gate must never read as a disabled one: None
                    # here means the security task could not be created or
                    # read, which is a failure, not a waiver.
                    if security is None or security.status is not TaskStatus.PASSED:
                        shown = (security.status.value if security is not None
                                 else "unavailable")
                        return WorkflowResult(
                            workflow_id, False,
                            f"Security review was {shown} after repair; "
                            "the repaired change is not ready.",
                        )
                ready = assert_tasks_ready(
                    self.state.list_tasks(workflow_id), workflow_id,
                    verification_task_id=verification.id, review_task_id=review.id,
                )
                return WorkflowResult(workflow_id, True, ready.summary())
            if review.status is not TaskStatus.FAILED:
                return WorkflowResult(
                    workflow_id, False,
                    f"Review was {review.status.value} after repair; no further "
                    f"review failure was demonstrated.",
                )
        last_verdict = (verdict_summary_for_task(review) or {}).get("verdict", "missing")
        last_blocking = (verdict_summary_for_task(review) or {}).get("blocking") or []
        blocked = "; ".join(str(item) for item in last_blocking[:5])
        suffix = f" Last verdict: {last_verdict}."
        if blocked:
            suffix += f" Blocking findings: {redact_text(blocked)[:1000]}"
        return WorkflowResult(
            workflow_id, False,
            f"Review did not pass after {max_rounds} review round(s); the workflow is BLOCKED." + suffix,
        )

    async def run_high_level(
        self,
        description: str,
        working_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> WorkflowResult:
        workflow_id, tasks = self.create_standard_workflow(description)
        await self.execute(workflow_id, working_directory, cancel_event)
        verification = self.state.get_task(tasks[2].id)
        if verification.status is TaskStatus.PASSED and verification.verified:
            review = self.state.get_task(tasks[3].id)
            if review.status is TaskStatus.PASSED:
                if len(tasks) > 4 and _is_security_role(tasks[4].role):
                    security = self.state.get_task(tasks[4].id)
                    if security.status is not TaskStatus.PASSED:
                        return WorkflowResult(
                            workflow_id, False,
                            f"Security review was {security.status.value}; "
                            "the change is not ready.",
                        )
                # One authoritative READY rule: verification + review + evidence.
                ready = assert_tasks_ready(
                    self.state.list_tasks(workflow_id), workflow_id,
                    verification_task_id=verification.id, review_task_id=review.id,
                )
                return WorkflowResult(workflow_id, True, ready.summary())
            # Verification passed and the review rejected the change: a
            # demonstrated defect, so the same bounded repair policy that
            # covers a failed verification applies here.
            return await self._repair_failed_review(
                workflow_id, description, working_directory, cancel_event,
                tasks[1].id, review,
            )
        implementation = self.state.get_task(tasks[1].id)
        if implementation.status is not TaskStatus.PASSED:
            return WorkflowResult(workflow_id, False, "Implementation did not pass; verification was not run.")
        if verification.status is not TaskStatus.FAILED:
            # A repair cycle is warranted only by a DEMONSTRATED verification
            # failure. BLOCKED means verification could not obtain applicable
            # evidence (report UNVERIFIED) -- there is no failure to repair, and
            # sending a "Repair the configured verification failure" task would
            # re-introduce exactly the semantic collapse UNVERIFIED exists to
            # prevent: "I could not prove this succeeded" becoming "this failed".
            # Nothing was demonstrated wrong, so there is nothing to fix and no
            # review to gate. An operator needs applicable verification, not a
            # repair loop against an absent signal.
            return WorkflowResult(
                workflow_id, False,
                f"Verification was {verification.status.value}; no verification "
                f"failure was demonstrated, so no repair was attempted.",
            )
        for _ in range(self.config.max_repair_cycles):
            repair = self.state.add_task(Task("Repair the configured verification failure.\n" + (verification.result or ""),
                                              "debugging", workflow_id, dependencies=(implementation.id,),
                                              max_attempts=self.config.max_attempts))
            reverify = self.state.add_task(Task("Re-run configured project verification commands.", "verification", workflow_id,
                                                dependencies=(repair.id,), max_attempts=1))
            final_review = self.state.add_task(Task(f"Review repaired change for: {description}", "review", workflow_id,
                                                    dependencies=(reverify.id,), max_attempts=self.config.max_attempts))
            await self.execute(workflow_id, working_directory, cancel_event)
            verification = self.state.get_task(reverify.id)
            final_review = self.state.get_task(final_review.id)
            if verification.status is TaskStatus.PASSED and verification.verified:
                if final_review.status is TaskStatus.PASSED:
                    if getattr(self.config, "security_review", False):
                        security = await self._gate_repair_security(
                            workflow_id, description, working_directory,
                            cancel_event, implementation.id, final_review.id,
                        )
                        # An enabled gate must never read as a disabled one:
                        # None means the security task could not be created or
                        # read, which is a failure, not a waiver.
                        if security is None or security.status is not TaskStatus.PASSED:
                            return WorkflowResult(
                                workflow_id, False,
                                "Repair or review did not pass; the security "
                                "review could not be completed.",
                            )
                    # One authoritative READY rule, scoped to this repair cycle's
                    # verification/review pair.
                    ready = assert_tasks_ready(
                        self.state.list_tasks(workflow_id), workflow_id,
                        verification_task_id=verification.id,
                        review_task_id=final_review.id,
                    )
                    return WorkflowResult(workflow_id, True, ready.summary())
                return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
        return WorkflowResult(workflow_id, False, "Repair or review did not pass.")
