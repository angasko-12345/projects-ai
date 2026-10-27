"""AgentOpsController: config, repository-root resolution and the one-operation lifecycle."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from .. import gui_controller as _ns

EventCallback = Callable[[dict[str, object]], None]


class ControllerBase:
    """Config, repository-root resolution and the one-operation lifecycle."""

    def _load_config(self) -> AppConfig:
        return self.config if self.config is not None else _ns.load_config(self.config_path)

    def _operation_root(self, directory: str | Path) -> Path:
        # Cache git-root lookups: the GUI resolves this on every poll, and
        # each miss spawns a git process.  Roots essentially never move
        # within a controller's lifetime; failures are NOT cached.  The
        # lock makes the miss path single-flight so concurrent pollers do
        # not each spawn git for the same key.
        key = str(directory)
        with self._root_cache_lock:
            cached = self._root_cache.get(key)
            if cached is not None:
                return Path(cached)
            try:
                root = _ns.GitWorktreeManager().repository_root(directory)
            except _ns.GitError:
                return Path(directory)
            self._root_cache[key] = str(root)
            return root

    def _state_path(self, directory: str | Path) -> Path:
        if self.state_path is not None:
            return self.state_path
        return self._operation_root(directory) / ".agentops" / "state.sqlite"

    def _artifact_root(self, directory: str | Path) -> Path:
        return self._state_path(directory).parent / "artifacts"

    def cancel(self) -> None:
        """Request cancellation of the current operation, if one exists."""
        self._cancel_event.set()

    def _begin_operation(self) -> threading.Event:
        with self._operation_lock:
            if self._active:
                raise RuntimeError("An AgentOps operation is already running.")
            self._active = True
            # Fresh identity per operation: _end_operation's identity check
            # must reject a stale worker's event once a newer operation runs.
            self._cancel_event = threading.Event()
            return self._cancel_event

    def _end_operation(self, event: threading.Event) -> None:
        if event is self._cancel_event:
            with self._operation_lock:
                self._active = False
