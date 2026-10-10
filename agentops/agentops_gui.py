"""Package-aware launcher for the frozen AgentOps desktop executable.

PyInstaller entry point for ``AgentOps.exe``. The GUI is the application's
default and only mode: double-clicking the executable opens the desktop client
with no terminal, no ``cd``, and no separately installed Python. The CLI is
unaffected and remains available as ``python -m agentops``.
"""

from __future__ import annotations


def main() -> int:
    """Run the desktop client; see :mod:`agentops.gui.launcher`."""
    from agentops.gui.launcher import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())