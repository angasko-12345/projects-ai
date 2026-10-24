"""Locations of bundled read-only resources.

A PyInstaller one-file build unpacks the bundle into ``sys._MEIPASS``; a
source checkout and an installed package keep those files next to the
``agentops`` package instead. Modules that read packaged data resolve it
through :func:`resource_path` so neither case depends on the current working
directory.

Standard-library only; the sole I/O is checking a preferred filesystem
candidate, so a directory or stale file there cannot shadow a packaged
resource.
"""

from __future__ import annotations

import json
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


def _is_bundled_config(path: Path) -> bool:
    """True when ``path`` is a real, parseable bundled ``agents.yaml``.

    ``resource_path`` prefers the filesystem next to the package. A directory,
    an empty file, or a stale/unparseable file left at that location is not a
    bundled config and must fall through to the packaged resource. The
    parseability contract mirrors ``config._load_data`` so ``resource_path``
    never rejects a preferred file the loader would have accepted: JSON-valid
    YAML parses under the standard library alone, and conventional YAML parses
    when the optional PyYAML extra is installed. A mapping root is required.
    """
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):  # missing/unreadable/undecodable candidate
        return False
    return _parses_to_mapping(text)


def _parses_to_mapping(text: str) -> bool:
    """True when ``text`` parses to a mapping on the same terms as the loader."""
    try:
        result = json.loads(text)
    except ValueError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError:
            return False
        try:
            result = yaml.safe_load(text)
        except yaml.YAMLError:
            return False
    return isinstance(result, dict)


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled resource, e.g. ``("agents", "agents.yaml")``."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).joinpath(*parts)

    # First try filesystem path (source checkout or editable install). The
    # config must be a real, parseable file; otherwise use the packaged copy.
    fs_path = bundle_root().joinpath(*parts)
    if parts == ("agents", "agents.yaml"):
        if _is_bundled_config(fs_path):
            return fs_path
    elif fs_path.exists():
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
