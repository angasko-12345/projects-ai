"""Locations of bundled read-only resources.

A PyInstaller one-file build unpacks the bundle into ``sys._MEIPASS``; a
source checkout and an installed package keep those files next to the
``agentops`` package instead. Modules that read packaged data resolve it
through :func:`resource_path` so neither case depends on the current working
directory.

Standard-library only, no I/O beyond ``sys``: this is a leaf helper.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundle_root() -> Path:
    """Directory holding bundled data files.

    ``sys._MEIPASS`` when frozen, otherwise the directory that contains the
    ``agentops`` package. Never the current working directory.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled resource, e.g. ``("agents", "agents.yaml")``."""
    return bundle_root().joinpath(*parts)