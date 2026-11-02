"""Service facade used by the AgentOps desktop client.

This module keeps long-running operations off the Qt GUI thread and
provides a small cancellation contract.  It delegates all substantive work to
the existing AgentOps runner, workflow, Git, logging, and persistence modules.

Layout (a package since phase 2c of the refactor; the import path and the
public names are unchanged):

- ``serializers``: every ``serialize_*`` function (domain object -> dict).
- ``base``: ``EventCallback``, config/root resolution, the operation lifecycle.
- ``agents``, ``workflows``, ``tasks``, ``verification``, ``records``,
  ``worktrees``: per-concern mixins.
- ``controller``: the ``AgentOpsController`` dataclass composing them.

Patch targets stay here. The mixins and serializers resolve collaborators
(``StateStore``, ``GitWorktreeManager``, ``WorkflowEngine``, ``serialize_*``,
...) through this package's namespace at call time (``_ns.NAME``), so
``patch("agentops.gui_controller.NAME")`` still reaches the controller.
These re-exports are therefore load-bearing: do not prune them as unused.
"""

# ruff: noqa: F401

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Callable

from ..agent_run import AgentRun, AgentRunStatus, GitRunMetadataCollector
from ..artifacts import ArtifactError, ArtifactStore
from ..config import AppConfig, load_config
from ..finalize import (
    finalize_for_outcome,
    implementation_passed,
    record_worktree_provenance,
)
from ..git import GitError, GitWorktreeManager, Worktree, WorktreeRef
from ..tasks import Task, Workflow
from ..logging import LogManager
from ..registry import AgentRegistry
from ..runner import AgentRunner, OperationCancelled
from ..state import StateStore
from ..verification import Verifier
from ..verification_kernel import VerificationKernel
from ..execution_model import assess_workflow_readiness
from ..failure import Failure
from ..verification_model import VerificationCheck, VerificationReport, VerificationRun
from ..workflow import WorkflowEngine, STANDARD_TASK_ROLES, verdict_summary_for_task
from .serializers import (
    serialize_failure,
    serialize_task,
    serialize_workflow,
    serialize_worktree_ref,
    serialize_verification_check,
    serialize_verification_run,
    serialize_verification_report,
    serialize_agent_run,
)
from .base import EventCallback
from .controller import AgentOpsController
