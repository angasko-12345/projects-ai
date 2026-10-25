"""Layered configuration for where the collector data lives.

Some users keep collected per-agent telemetry trees outside the agents' own
default locations (an archive of CLI home directories, a shared drive, a
converted store). Those trees are resolved here, one layer at a time:

1. ``TOKEN_TRACKER_DATA_DIR`` environment variable
2. ``data_dir`` in the user's config file (see :func:`config_file`)
3. the user's home directory

A configured location that is not a directory is an error, not a silent
fall back to the default: a typo would otherwise look exactly like "that
agent is not installed".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_VAR = "TOKEN_TRACKER_DATA_DIR"
CONFIG_KEY = "data_dir"


class ConfigError(Exception):
    """A configured data location is unusable."""


def config_file() -> Path:
    """The user's config file: ``%APPDATA%`` on Windows, XDG elsewhere."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "ai-token-tracker" / "config.json"


def _configured() -> tuple[str, str] | None:
    """(raw value, where it was set) from the environment, else the config file."""
    env = os.environ.get(ENV_VAR)
    if env:
        return env, ENV_VAR
    path = config_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f'{path} must contain a JSON object like {{"{CONFIG_KEY}": "..."}}')
    value = data.get(CONFIG_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{path}: "{CONFIG_KEY}" must be a non-empty string')
    return value, str(path)


def data_dir() -> Path:
    """Root of the per-agent data trees, resolved through the layers above.

    Raises :class:`ConfigError` when a configured directory is missing so the
    caller can report the misconfiguration instead of an empty scan.
    """
    configured = _configured()
    if configured is None:
        return Path.home()
    value, source = configured
    path = Path(value).expanduser()
    if not path.is_dir():
        raise ConfigError(f"{source} points at {path}, which is not an existing directory")
    return path
