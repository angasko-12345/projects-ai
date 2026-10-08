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
from importlib import resources
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
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).joinpath(*parts)
    
    # First try filesystem path (source checkout or editable install)
    fs_path = bundle_root().joinpath(*parts)
    if fs_path.exists():
        return fs_path
    
    # Fall back to importlib.resources for installed wheel
    if parts[0] == "agents":
        # agents.yaml is in agentops.agents package
        try:
            ref = resources.files("agentops.agents")
            if len(parts) > 1:
                ref = ref.joinpath(*parts[1:])
            # Use as_file to get a real filesystem path (works for both wheel and source)
            with resources.as_file(ref) as file_path:
                return Path(file_path)
        except (ModuleNotFoundError, AttributeError, FileNotFoundError):
            pass
    elif parts[0] == "agentops" and parts[1] == "gui" and parts[2] == "assets":
        # GUI assets are in agentops.gui.assets package
        try:
            ref = resources.files("agentops.gui.assets")
            if len(parts) > 3:
                ref = ref.joinpath(*parts[3:])
            with resources.as_file(ref) as file_path:
                return Path(file_path)
        except (ModuleNotFoundError, AttributeError, FileNotFoundError):
            pass
    # Final fallback to filesystem path (may not exist)
    return fs_path