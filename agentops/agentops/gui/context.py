"""Shared view context and async read helper.

Anything that talks to ``AgentOpsController`` gets reads through
:class:`ViewContext` -> ``ControllerBridge`: work runs on a worker thread
and results come back as queued Qt signals. Stale responses (an older request
for the same key arriving after a newer one) are dropped by token sequence.

This lives outside ``views`` because detail panels and the shell need it too.
``views/base.py`` re-exports it for views; keeping it in ``views`` would make
``views/__init__`` load every view on a plain detail-panel import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .bridge import ControllerBridge
from .settings import AppSettings


@dataclass(frozen=True)
class ViewContext:
    """Everything a view or panel may touch outside its own widgets."""

    bridge: ControllerBridge
    repository: Callable[[], str]                    # current repository path
    navigate: Callable[[str], None]                  # switch shell view by id
    toast: Callable[..., None]                       # toast(title, message="", kind="info")
    settings: Callable[[], AppSettings]              # latest persisted settings snapshot
    open_task: Callable[[str, str], None]            # workflow_id, task_id -> task detail
    open_run: Callable[[str], None]                  # run_id -> run detail
    operation_active: Callable[[], bool]             # shell-level background op state
    update_settings: Callable[[dict], None]          # partial settings update + persist

    @property
    def controller(self) -> object:
        return self.bridge.controller


class AsyncMixin:
    """Token-sequenced async submits; usable by views and standalone widgets."""

    def _init_async(self, bridge: ControllerBridge) -> None:
        self._bridge = bridge
        self._seq: dict[str, int] = {}
        self._pending: dict[str, tuple[str, Callable, Callable | None]] = {}
        self._alive = True
        bridge.result_ready.connect(self._on_bridge_result)
        bridge.error_ready.connect(self._on_bridge_error)

    def submit(
        self,
        key: str,
        work: Callable[[], object],
        on_ok: Callable[[object], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Run ``work`` off-thread; only the newest request per ``key`` may
        deliver, so stale responses from an earlier refresh are dropped."""
        if not getattr(self, "_alive", False):
            return
        sequence = self._seq.get(key, 0) + 1
        self._seq[key] = sequence
        token = f"{id(self)}:{key}:{sequence}"
        self._pending[token] = (key, on_ok, on_error)
        self._bridge.submit(token, work)

    def invalidate(self, key: str) -> None:
        """Drop any in-flight delivery for ``key``."""
        self._seq[key] = self._seq.get(key, 0) + 1
        for token in [token for token, entry in self._pending.items() if entry[0] == key]:
            self._pending.pop(token, None)

    def _shutdown_async(self) -> None:
        self._alive = False
        self._pending.clear()

    def _on_bridge_result(self, token: str, value: object) -> None:
        entry = self._pending.pop(token, None)
        if entry is None or not self._alive:
            return
        key, on_ok, _ = entry
        if self._sequence_of(token) != self._seq.get(key):
            return  # superseded by a newer request for the same key
        try:
            on_ok(value)
        except RuntimeError:
            return  # wrapped C++ widget was destroyed between delivery and call

    def _on_bridge_error(self, token: str, message: str) -> None:
        entry = self._pending.pop(token, None)
        if entry is None or not self._alive:
            return
        key, _, on_error = entry
        if self._sequence_of(token) != self._seq.get(key):
            return
        try:
            if on_error is not None:
                on_error(message)
            else:
                self.handle_default_error(message)
        except RuntimeError:
            return

    def handle_default_error(self, message: str) -> None:
        """Fallback when a submit supplies no ``on_error`` (silent by default;
        views override to surface the failure)."""

    @staticmethod
    def _sequence_of(token: str) -> int:
        try:
            return int(token.rsplit(":", 1)[-1])
        except ValueError:
            return -1

