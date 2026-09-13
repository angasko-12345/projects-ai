"""Configuration loading and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentConfig:
    name: str
    command: str
    args: tuple[str, ...]
    roles: tuple[str, ...] = ()
    timeout_seconds: int = 900


@dataclass(frozen=True)
class AppConfig:
    agents: dict[str, AgentConfig]
    role_preferences: dict[str, tuple[str, ...]]
    verification_commands: tuple[tuple[str, ...], ...] = ()
    max_attempts: int = 2
    concurrency: int = 2


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "agents" / "agents.yaml"


def _load_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as error:
            raise ValueError("Configuration must be JSON-valid YAML unless PyYAML is installed.") from error
        result = yaml.safe_load(text)
    if not isinstance(result, dict):
        raise ValueError("Configuration root must be a mapping.")
    return result


def _command(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a non-empty list of strings.")
    return tuple(value)


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data = _load_data(config_path)
    raw_agents = data.get("agents", {})
    if not isinstance(raw_agents, dict):
        raise ValueError("agents must be a mapping.")
    agents: dict[str, AgentConfig] = {}
    for name, raw in raw_agents.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise ValueError("Each agent must have a string name and mapping configuration.")
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError(f"agents.{name}.command must be a non-empty string.")
        args = raw.get("args", ["{prompt}"])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError(f"agents.{name}.args must be a list of strings.")
        roles = raw.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
            raise ValueError(f"agents.{name}.roles must be a list of strings.")
        timeout = raw.get("timeout_seconds", 900)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"agents.{name}.timeout_seconds must be a positive integer.")
        agents[name] = AgentConfig(name, command, tuple(args), tuple(roles), timeout)
    roles_raw = data.get("role_preferences", {})
    if not isinstance(roles_raw, dict):
        raise ValueError("role_preferences must be a mapping.")
    role_preferences: dict[str, tuple[str, ...]] = {}
    for role, preferences in roles_raw.items():
        if not isinstance(role, str) or not isinstance(preferences, list) or not all(isinstance(item, str) for item in preferences):
            raise ValueError("role preferences must map strings to lists of strings.")
        role_preferences[role] = tuple(preferences)
    verification_raw = data.get("verification", {}).get("commands", [])
    if not isinstance(verification_raw, list):
        raise ValueError("verification.commands must be a list.")
    verification_commands = tuple(_command(command, "verification.commands item") for command in verification_raw)
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping.")
    max_attempts = runtime.get("max_attempts", 2)
    concurrency = runtime.get("concurrency", 2)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("runtime.max_attempts must be at least one.")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("runtime.concurrency must be at least one.")
    return AppConfig(agents, role_preferences, verification_commands, max_attempts, concurrency)
