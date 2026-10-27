"""Dependency-aware task orchestration with agent fallback and repairs.

This package replaces the former single-module ``agentops/workflow.py``.
Every name that module defined or imported is re-exported here, so
``from agentops.workflow import X`` keeps working for all X.
"""

from __future__ import annotations

import asyncio as asyncio
import inspect as inspect
import re as re
import subprocess as subprocess
import sys as sys
import threading as threading
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from time import monotonic as monotonic, time as wall_clock  # noqa: F401 (re-export)
from uuid import uuid4 as uuid4
from ..agent_run import (
    AgentRun as AgentRun,
    AgentRunContext as AgentRunContext,
    AgentRunMetadata as AgentRunMetadata,
    AgentRunObserver as AgentRunObserver,
    AgentRunOutcome as AgentRunOutcome,
    AgentRunRelationship as AgentRunRelationship,
    AgentRunStatus as AgentRunStatus,
    GitRunMetadataCollector as GitRunMetadataCollector,
)
from ..agent_result import (
    AgentResultStatus as AgentResultStatus,
    coerce_agent_result as coerce_agent_result,
    parse_agent_result as parse_agent_result,
)
from ..evidence import TaskEvidence as TaskEvidence, expected_evidence as expected_evidence
from ..execution_model import (
    StateTransitionError as StateTransitionError,
    WorkflowReadiness as WorkflowReadiness,
    assert_report_consistent as assert_report_consistent,
    assert_task_completion as assert_task_completion,
    assert_tasks_ready as assert_tasks_ready,
    assess_workflow_readiness as assess_workflow_readiness,
)
from ..failure import (
    Failure as Failure,
    FailureCategory as FailureCategory,
    FailureClassifier as FailureClassifier,
    FailureEvidence as FailureEvidence,
    FailureSeverity as FailureSeverity,
    FailureSource as FailureSource,
    InterruptionContext as InterruptionContext,
    RepairPlan as RepairPlan,
    RetryPolicy as RetryPolicy,
    build_retry_prompt as build_retry_prompt,
    new_failure_id as new_failure_id,
    parse_retry_after_seconds as parse_retry_after_seconds,
)
from ..logging import redact_text as redact_text
from ..persistence import DegradationRecorder as DegradationRecorder, event_emitter as event_emitter
from ..security import findings_for_prompt as findings_for_prompt, scan_many as scan_many
from ..verdict import parse_verdict as parse_verdict
from ..config import AppConfig as AppConfig
from ..headroom import (
    REJECT as REJECT,
    GateVerdict as GateVerdict,
    HeadroomGate as HeadroomGate,
    format_utc as format_utc,
    merge_verdicts as merge_verdicts,
)
from ..headroom_source import build_headroom_gate as build_headroom_gate
from ..events import Event as Event, EventSeverity as EventSeverity, EventType as EventType
from ..registry import AgentRegistry as AgentRegistry, DetectedAgent as DetectedAgent
from ..repo_inspection import RepositoryInspector as RepositoryInspector
from ..routing import AgentRouter as AgentRouter
from ..runner import (
    AgentRunner as AgentRunner,
    OperationCancelled as OperationCancelled,
    RunResult as RunResult,
)
from ..state import StateStore as StateStore
from ..tasks import Task as Task, TaskStatus as TaskStatus, utc_now as utc_now
from ..verification import Verifier as Verifier
from ..verification_kernel import VerificationKernel as VerificationKernel
from ..verification_model import VerificationReportStatus as VerificationReportStatus

from .support import (
    _scrub_structured as _scrub_structured,
    _RecordingObserver as _RecordingObserver,
    WorkflowResult as WorkflowResult,
    _is_git_working_tree as _is_git_working_tree,
    STANDARD_TASK_ROLES as STANDARD_TASK_ROLES,
    HANDOFF_RESULT_CHARS as HANDOFF_RESULT_CHARS,
    HANDOFF_TOTAL_CHARS as HANDOFF_TOTAL_CHARS,
)
from .verdicts import (
    _requires_review_verdict as _requires_review_verdict,
    verdict_summary_for_task as verdict_summary_for_task,
    _safe_workflow_structured as _safe_workflow_structured,
    _SUCCESS_KEYS as _SUCCESS_KEYS,
    _FALSE_LIKE as _FALSE_LIKE,
    _is_verdict_only as _is_verdict_only,
    _payload_denies_success as _payload_denies_success,
    _stdout_candidate_dicts as _stdout_candidate_dicts,
    _structured_agent_status as _structured_agent_status,
    _explicit_success_denied as _explicit_success_denied,
    _review_payload_denies_success as _review_payload_denies_success,
    _review_output_denies_success as _review_output_denies_success,
)
from .security import (
    _is_security_role as _is_security_role,
    _local_security_scan as _local_security_scan,
    _ROLE_SCOPE_MAX_CHARS as _ROLE_SCOPE_MAX_CHARS,
    _role_scope_text as _role_scope_text,
)
from .engine import WorkflowEngine as WorkflowEngine
