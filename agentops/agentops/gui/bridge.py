"""Thread bridge between Qt widgets and ``AgentOpsController``.

Controller reads are synchronous (SQLite + occasional git calls). Running
them on the GUI thread would freeze the window, so views submit callables to
a small worker pool and receive results back through Qt signals, which are
delivered on the GUI thread. The controller's own operation callbacks
(``run_agent``/``run_task``) already execute on controller threads; they are
re-emitted here as ``operation_event`` so the shell can drive status,
polling, and notifications without touching widgets off-thread.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QObject, Signal


class ControllerBridge(QObject):
    """Runs controller work off the GUI thread and delivers results by signal."""

    result_ready = Signal(str, object)   # request token -> value
    error_ready = Signal(str, str)       # request token -> error message
    operation_event = Signal(object)     # controller event dict (run/task lifecycle)

    def __init__(self, controller: object, parent: QObject | None = None, max_workers: int = 4):
        super().__init__(parent)
        self._controller = controller
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="agentops-gui"
        )
        self._lock = threading.Lock()
        self._shutdown = False

    @property
    def controller(self) -> object:
        return self._controller

    def submit(self, token: str, work: Callable[[], object]) -> None:
        """Run ``work()`` on a worker thread; deliver via result/error signals."""

        def run() -> None:
            try:
                value = work()
            except Exception as error:  # noqa: BLE001 - boundary: all failures surface to the view
                with self._lock:
                    if self._shutdown:
                        return
                self.error_ready.emit(token, str(error))
            else:
                with self._lock:
                    if self._shutdown:
                        return
                self.result_ready.emit(token, value)

        with self._lock:
            if self._shutdown:
                return
            self._executor.submit(run)

    def operation_callback(self) -> Callable[[dict[str, object]], None]:
        """Callback to hand to ``controller.run_agent``/``run_task``.

        Safe to call from controller worker threads: it only emits a signal.
        """

        def emit(event: dict[str, object]) -> None:
            with self._lock:
                if self._shutdown:
                    return
            self.operation_event.emit(event)

        return emit

    def shutdown(self) -> None:
        """Stop accepting work; in-flight results are dropped."""
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)
