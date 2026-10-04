"""AgentOps desktop client (PySide6 presentation layer).

The package is the presentation boundary: widgets talk to
``AgentOpsController`` only through the bridge, never to SQLite, subprocesses,
Git, or the workflow engine directly. PySide6 is an optional desktop
dependency; the core runtime stays standard-library only.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["main"]


def main(config_path: str | Path | None = None) -> None:
    """Launch the desktop client.

    Raises ``ImportError`` with an actionable message when the optional
    PySide6 dependency is missing so the CLI can report it cleanly.
    """
    try:
        from .app import run
    except ImportError as error:
        raise ImportError(
            "The AgentOps desktop client requires PySide6; "
            "install it with 'pip install agentops[desktop]'."
        ) from error
    run(config_path)


if __name__ == "__main__":
    main()
