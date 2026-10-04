"""Base class shared by every shell view.

The view context and async submit machinery live in :mod:`agentops.gui.context`
so detail panels can use them without importing the view registry.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..bridge import ControllerBridge
from ..context import AsyncMixin, ViewContext

__all__ = ["AsyncMixin", "BaseView", "ControllerBridge", "ViewContext"]


class BaseView(QWidget, AsyncMixin):
    """Contract for every shell view: build, refresh, operation events."""

    view_id: str = ""
    title: str = ""

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)
        self._build()

    # -- construction -------------------------------------------------
    def _build(self) -> None:
        raise NotImplementedError

    # -- repository ---------------------------------------------------
    def repository(self) -> str:
        return str(self.ctx.repository()).strip()

    def handle_default_error(self, message: str) -> None:
        self.ctx.toast("Request failed", message, "error")

    # -- lifecycle ----------------------------------------------------
    def refresh(self) -> None:
        """Reload view data. The shell calls this on activation, on refresh
        shortcuts, and periodically while an operation runs."""

    def on_operation_event(self, event: dict[str, object]) -> None:
        """Forwarded controller event while a global operation is active."""

    def shutdown(self) -> None:
        self._shutdown_async()
