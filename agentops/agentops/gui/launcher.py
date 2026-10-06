"""Process bootstrap for the packaged AgentOps desktop application.

``AgentOps.exe`` is a windowed build: it has no console, so ``sys.stdout`` and
``sys.stderr`` are ``None`` and an incidental write raises ``AttributeError``
instead of printing. A user double-clicking the executable sees no output at
all, and a startup failure closes silently.

This module makes that process well behaved: usable stdio, an append-only log
under the platform settings directory, a root logging handler pointed at it,
and a native message box when startup fails. It imports nothing beyond the
standard library so it still works when PySide6 itself is the missing piece.

Qt-free: this module is importable and testable without a display.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Callable

from .settings import settings_path

LOG_FILE_NAME = "agentops.log"
WINDOW_TITLE = "AgentOps"
_MB_ICONERROR = 0x10


class _NullStream:
    """Stand-in for the console streams a windowed build does not have."""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("No console is attached to this build.")


def ensure_stdio() -> None:
    """Replace absent console streams so incidental writes cannot crash."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())


def log_path() -> Path:
    """``%APPDATA%\\AgentOps\\logs\\agentops.log`` (see :mod:`.settings`)."""
    return settings_path().parent / "logs" / LOG_FILE_NAME


def write_log(message: str, target: Path | None = None) -> bool:
    """Append a UTC-stamped line. Never raises: logging must not break a run."""
    destination = target or log_path()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        return False
    return True


def install_handlers() -> Path:
    """Install stdio, logging, and exception handlers. Returns the log path."""
    ensure_stdio()
    destination = log_path()
    try:
        logging.basicConfig(
            filename=str(destination),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            encoding="utf-8",
            errors="replace",
            force=True,
        )
    except (OSError, ValueError):
        pass
    sys.excepthook = _log_unhandled_exception
    threading.excepthook = _log_unhandled_thread_exception
    return destination


def _log_unhandled_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    write_log("unhandled exception:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))


def _log_unhandled_thread_exception(args: threading.ExceptHookArgs) -> None:
    _log_unhandled_exception(args.exc_type, args.exc_value, args.exc_traceback)


def show_error_box(title: str, message: str) -> None:
    """Show a native modal error. Uses ``MessageBoxW`` so it needs no Qt."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, _MB_ICONERROR)
            return
        except Exception:  # noqa: BLE001 - a dialog must never raise
            pass
    print(f"{title}: {message}", file=sys.stderr)


def report_fatal(error: BaseException, detail: str = "", box: Callable[[str, str], None] | None = None) -> None:
    """Log a fatal startup error and tell the user where the details went."""
    write_log(f"fatal: {type(error).__name__}: {error}")
    text = f"{type(error).__name__}: {error}"
    (box or show_error_box)(WINDOW_TITLE, f"{text}\n\n{detail}" if detail else text)


def run() -> int:
    """Launch the GUI. Returns a process exit code instead of raising."""
    destination = install_handlers()
    write_log(
        f"starting (python={sys.version.split()[0]}, frozen={bool(getattr(sys, 'frozen', False))})",
        destination,
    )
    try:
        from . import main
    except ImportError as error:
        report_fatal(
            error,
            "AgentOps could not load its desktop client.\n\n"
            f"Details were written to:\n{destination}",
        )
        return 1
    try:
        main()
    except Exception as error:  # noqa: BLE001 - no console exists to report this
        write_log("startup failed:\n" + traceback.format_exc(), destination)
        report_fatal(error, f"AgentOps could not start.\n\nDetails were written to:\n{destination}")
        return 1
    write_log("exited cleanly", destination)
    return 0