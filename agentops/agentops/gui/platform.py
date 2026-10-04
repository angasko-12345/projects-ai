"""Small platform helpers for the desktop client (file-manager opening)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_path_external(path: str | Path) -> None:
    """Reveal a local path in the platform file manager (Windows/macOS/Linux)."""
    target = Path(path)
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(str(target))  # noqa: S606 - local GUI file navigation only
    elif sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    else:
        subprocess.run(["xdg-open", str(target)], check=False)
