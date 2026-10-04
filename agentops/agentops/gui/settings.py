"""Persistent desktop-client settings (recent repositories, layout memory).

Qt-free: plain dataclasses plus atomic JSON persistence, so the settings
contract is testable without PySide6. Paths follow the platform convention
(``%APPDATA%\\AgentOps`` on Windows, ``$XDG_CONFIG_HOME/agentops`` elsewhere)
and never touch the target repository's state.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_VERSION = 1
RECENT_REPOSITORY_LIMIT = 8


@dataclass
class AppSettings:
    version: int = SETTINGS_VERSION
    repository: str | None = None
    config_path: str | None = None
    recent_repositories: list[str] = field(default_factory=list)
    geometry: str | None = None
    maximized: bool = False
    sidebar_collapsed: bool = False
    last_view: str = "dashboard"

    def touch_repository(self, path: str, limit: int = RECENT_REPOSITORY_LIMIT) -> None:
        """Move ``path`` to the front of the recent list, deduped and capped."""
        cleaned = str(path).strip()
        if not cleaned:
            return
        self.repository = cleaned
        recent = [item for item in [cleaned, *self.recent_repositories] if item]
        seen: list[str] = []
        for item in recent:
            if item not in seen:
                seen.append(item)
        self.recent_repositories = seen[:limit]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "repository": self.repository,
            "config_path": self.config_path,
            "recent_repositories": list(self.recent_repositories),
            "geometry": self.geometry,
            "maximized": self.maximized,
            "sidebar_collapsed": self.sidebar_collapsed,
            "last_view": self.last_view,
        }

    @classmethod
    def from_dict(cls, data: object) -> "AppSettings":
        """Tolerant load: unknown or malformed fields fall back to defaults."""
        settings = cls()
        if not isinstance(data, dict):
            return settings
        repository = data.get("repository")
        if isinstance(repository, str) and repository:
            settings.touch_repository(repository)
        config_path = data.get("config_path")
        if isinstance(config_path, str):
            settings.config_path = config_path
        recent = data.get("recent_repositories")
        if isinstance(recent, list):
            settings.recent_repositories = [
                str(item) for item in recent[:RECENT_REPOSITORY_LIMIT] if isinstance(item, str) and item
            ]
        geometry = data.get("geometry")
        if isinstance(geometry, str) and geometry:
            settings.geometry = geometry
        settings.maximized = bool(data.get("maximized", False))
        settings.sidebar_collapsed = bool(data.get("sidebar_collapsed", False))
        last_view = data.get("last_view")
        if isinstance(last_view, str) and last_view:
            settings.last_view = last_view
        return settings


def settings_path() -> Path:
    """Platform-conventional location of ``settings.json``."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "AgentOps" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "agentops" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings; a missing or corrupt file yields defaults, never raises."""
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    return AppSettings.from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Atomically persist settings; best effort, read errors never raise."""
    target = path or settings_path()
    temp_name: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".settings-", suffix=".tmp"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(settings.to_dict(), handle, indent=2, sort_keys=True)
        os.replace(temp_name, target)
        temp_name = None
    except OSError:
        pass
    finally:
        if temp_name is not None:
            with suppress(OSError):
                os.unlink(temp_name)
