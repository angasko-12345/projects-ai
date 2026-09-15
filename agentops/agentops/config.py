"""Configuration loading and validation."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .verification_model import (
    VerificationCheckClass,
    VerificationCheckSpec,
    VerificationExecutionPolicy,
    VerificationProfile,
    VerificationProfileMode,
)


@dataclass(frozen=True)
class AgentConfig:
    name: str
    command: str
    args: tuple[str, ...]
    roles: tuple[str, ...] = ()
    timeout_seconds: int = 900
    enabled: bool = True
    model: str | None = None
    # Declared extra capabilities (Track A2). Baseline coding/planning/
    # review is derived from `roles` by the adapter; only list here what
    # the CLI demonstrably provides — never fabricate entries.
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppConfig:
    agents: dict[str, AgentConfig]
    role_preferences: dict[str, tuple[str, ...]]
    verification_commands: tuple[tuple[str, ...], ...] = ()
    max_attempts: int = 2
    concurrency: int = 2
    max_repair_cycles: int = 1
    pass_env_names: tuple[str, ...] = ()
    pass_env_prefixes: tuple[str, ...] = ()
    verification_profiles: dict[str, VerificationProfile] = field(default_factory=dict)
    default_verification_profile: str | None = None
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    backoff_factor: float = 2.0


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


def _verification_check(value: Any, profile_name: str) -> VerificationCheckSpec:
    if not isinstance(value, dict):
        raise ValueError(f"verification.profiles.{profile_name}.checks must be a list of mappings.")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"verification.profiles.{profile_name}.checks need a non-empty string name.")
    check_class = value.get("class")
    try:
        parsed_class = VerificationCheckClass(check_class)
    except ValueError as error:
        allowed = ", ".join(sorted(item.value for item in VerificationCheckClass))
        raise ValueError(
            f"verification.profiles.{profile_name}.checks.{name}.class must be one of: {allowed}."
        ) from error
    command = _command(value.get("command"), f"verification.profiles.{profile_name}.checks.{name}.command")
    working_directory = value.get("working_directory")
    if working_directory is not None and (not isinstance(working_directory, str) or not working_directory.strip()):
        raise ValueError(
            f"verification.profiles.{profile_name}.checks.{name}.working_directory must be a non-empty string."
        )
    timeout = value.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
        raise ValueError(
            f"verification.profiles.{profile_name}.checks.{name}.timeout_seconds must be a positive integer."
        )
    required = value.get("required", True)
    if not isinstance(required, bool):
        raise ValueError(
            f"verification.profiles.{profile_name}.checks.{name}.required must be a boolean."
        )
    policy = value.get("policy", VerificationExecutionPolicy.SEQUENTIAL.value)
    try:
        parsed_policy = VerificationExecutionPolicy(policy)
    except ValueError as error:
        raise ValueError(
            f"verification.profiles.{profile_name}.checks.{name}.policy must be 'sequential' or 'parallel'."
        ) from error
    return VerificationCheckSpec(name, parsed_class, command, working_directory, timeout, required, parsed_policy)


def _verification_profile(name: Any, value: Any) -> VerificationProfile:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("verification.profiles needs non-empty string profile names.")
    if not isinstance(value, dict):
        raise ValueError(f"verification.profiles.{name} must be a mapping.")
    mode = value.get("mode", VerificationProfileMode.FAIL_FAST.value)
    try:
        parsed_mode = VerificationProfileMode(mode)
    except ValueError as error:
        raise ValueError(
            f"verification.profiles.{name}.mode must be 'fail_fast' or 'continue_on_failure'."
        ) from error
    concurrency = value.get("concurrency", 1)
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError(f"verification.profiles.{name}.concurrency must be a positive integer.")
    default_timeout = value.get("default_timeout_seconds", 300)
    if not isinstance(default_timeout, int) or default_timeout <= 0:
        raise ValueError(f"verification.profiles.{name}.default_timeout_seconds must be a positive integer.")
    raw_checks = value.get("checks", [])
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError(f"verification.profiles.{name}.checks must be a non-empty list.")
    checks = tuple(_verification_check(item, name) for item in raw_checks)
    names = [check.name for check in checks]
    if len(set(names)) != len(names):
        raise ValueError(f"verification.profiles.{name}.checks need unique check names.")
    return VerificationProfile(name, parsed_mode, concurrency, default_timeout, checks)


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
        if not isinstance(args, list) or not args or not all(isinstance(item, str) for item in args):
            raise ValueError(f"agents.{name}.args must be a non-empty list of strings.")
        if not any("{prompt}" in item for item in args):
            warnings.warn(f"agents.{name}.args does not include '{{prompt}}'; the prompt will not be passed.", stacklevel=2)
        roles = raw.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
            raise ValueError(f"agents.{name}.roles must be a list of strings.")
        timeout = raw.get("timeout_seconds", 900)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"agents.{name}.timeout_seconds must be a positive integer.")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"agents.{name}.enabled must be a boolean.")
        model = raw.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError(f"agents.{name}.model must be a string when provided.")
        capabilities = raw.get("capabilities", [])
        if (not isinstance(capabilities, list) or not all(isinstance(item, str) and item.strip() for item in capabilities)):
            raise ValueError(f"agents.{name}.capabilities must be a list of non-empty strings.")
        # Local import: agent_adapter owns the Capability vocabulary but
        # wraps AgentConfig, so a module-level import here would cycle.
        from .agent_adapter import KNOWN_CAPABILITIES
        for item in capabilities:
            if item not in KNOWN_CAPABILITIES:
                warnings.warn(
                    f"agents.{name}.capabilities has unrecognized capability '{item}'; "
                    "it will be passed through as-is.", stacklevel=2)
        agents[name] = AgentConfig(name, command, tuple(args), tuple(roles), timeout, enabled, model,
                                   tuple(capabilities))
    roles_raw = data.get("role_preferences", {})
    if not isinstance(roles_raw, dict):
        raise ValueError("role_preferences must be a mapping.")
    role_preferences: dict[str, tuple[str, ...]] = {}
    for role, preferences in roles_raw.items():
        if not isinstance(role, str) or not isinstance(preferences, list) or not all(isinstance(item, str) for item in preferences):
            raise ValueError("role preferences must map strings to lists of strings.")
        unknown = [item for item in preferences if item not in agents]
        if unknown:
            raise ValueError(f"role_preferences.{role} references unknown agents: {', '.join(unknown)}")
        role_preferences[role] = tuple(preferences)
    verification = data.get("verification", {})
    if not isinstance(verification, dict):
        raise ValueError("verification must be a mapping.")
    verification_raw = verification.get("commands", [])
    if not isinstance(verification_raw, list):
        raise ValueError("verification.commands must be a list.")
    verification_commands = tuple(_command(command, "verification.commands item") for command in verification_raw)
    profiles_raw = verification.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise ValueError("verification.profiles must be a mapping.")
    verification_profiles = {
        name: _verification_profile(name, raw) for name, raw in profiles_raw.items()
    }
    default_profile = verification.get("default_profile")
    if default_profile is not None and not isinstance(default_profile, str):
        raise ValueError("verification.default_profile must be a string when provided.")
    if verification_profiles:
        if default_profile is None:
            if len(verification_profiles) == 1:
                default_profile = next(iter(verification_profiles))
            else:
                raise ValueError("verification.default_profile is required when multiple profiles are configured.")
        if default_profile not in verification_profiles:
            raise ValueError(f"verification.default_profile references unknown profile: {default_profile}.")
    elif default_profile is not None:
        raise ValueError("verification.default_profile requires verification.profiles.")
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping.")
    max_attempts = runtime.get("max_attempts", 2)
    concurrency = runtime.get("concurrency", 2)
    max_repair_cycles = runtime.get("max_repair_cycles", 1)
    backoff_base = runtime.get("backoff_base_seconds", 1.0)
    backoff_max = runtime.get("backoff_max_seconds", 30.0)
    backoff_factor = runtime.get("backoff_factor", 2.0)
    pass_env = runtime.get("pass_env", {})
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("runtime.max_attempts must be at least one.")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("runtime.concurrency must be at least one.")
    if not isinstance(max_repair_cycles, int) or max_repair_cycles < 0:
        raise ValueError("runtime.max_repair_cycles must be zero or greater.")
    for label, value in (("backoff_base_seconds", backoff_base), ("backoff_max_seconds", backoff_max)):
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"runtime.{label} must be a non-negative number.")
    if not isinstance(backoff_factor, (int, float)) or backoff_factor < 1.0:
        raise ValueError("runtime.backoff_factor must be at least one.")
    if not isinstance(pass_env, dict):
        raise ValueError("runtime.pass_env must be a mapping.")
    names = pass_env.get("names", [])
    prefixes = pass_env.get("prefixes", [])
    if not isinstance(names, list) or not all(isinstance(item, str) and item for item in names):
        raise ValueError("runtime.pass_env.names must be a list of non-empty strings.")
    if not isinstance(prefixes, list) or not all(isinstance(item, str) and item for item in prefixes):
        raise ValueError("runtime.pass_env.prefixes must be a list of non-empty strings.")
    return AppConfig(agents, role_preferences, verification_commands, max_attempts, concurrency,
                     max_repair_cycles, tuple(names), tuple(prefixes),
                     verification_profiles, default_profile,
                     float(backoff_base), float(backoff_max), float(backoff_factor))
