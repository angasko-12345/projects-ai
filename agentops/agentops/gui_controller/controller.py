"""The composed AgentOpsController dataclass.

Behaviour lives in the per-concern mixins; this class only declares the
dataclass fields and composes them, so ``AgentOpsController(...)``, its
``__init__`` signature, repr and equality are what they were in the single
``gui_controller.py`` module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from .agents import AgentsMixin
from .base import ControllerBase
from .records import RecordsMixin
from .tasks import TasksMixin
from .verification import VerificationMixin
from .workflows import WorkflowsMixin
from .worktrees import WorktreesMixin


@dataclass
class AgentOpsController(
    AgentsMixin,
    WorkflowsMixin,
    TasksMixin,
    VerificationMixin,
    RecordsMixin,
    WorktreesMixin,
    ControllerBase,
):
    """Lifecycle and discovery facade for one AgentOps configuration."""

    config_path: Path | None = None
    state_path: Path | None = None
    config: AppConfig | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)
    _root_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _root_cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
