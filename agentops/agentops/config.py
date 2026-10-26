"""Configuration loading and validation."""

from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import resource_path
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
    display_name: str | None = None
    priority: int = 0
    metadata: dict[str, object] = field(default_factory=dict)
    version_command: tuple[str, ...] | None = None
    structured_output: bool | None = None
    cancellation_support: bool | None = None
    timeout_support: bool | None = None
    interactive_support: bool | None = None
    noninteractive_support: bool | None = None
    # U1: how the prompt reaches the child ("auto" | "argv" | "stdin" |
    # "file"; see agentops/prompt_transport.py).  `stdin_args` replaces
    # `args` when the prompt goes on stdin and, when set, declares that the
    # CLI reads its prompt from stdin.  Only set it for a CLI verified to do so.
    prompt_transport: str = "auto"
    stdin_args: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HeadroomConfig:
    """Opt-in HeadroomGate settings (top-level ``headroom`` key).

    Exactly one of ``file`` / ``command`` is set.  ``agents`` maps an agent
    name to the ai-token-tracker provider it draws on, optionally narrowed to
    window labels or limit ids.
    """

    agents: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]
    file: Path | None = None
    command: tuple[str, ...] | None = None
    command_timeout_seconds: float = 30.0
    cache_seconds: float = 60.0
    max_age_seconds: float = 900.0
    reject_at_percent: float = 95.0
    deprioritize_at_percent: float | None = 80.0
    unknown_policy: str = "deprioritize"


@dataclass(frozen=True)
class UsageLimitConfig:
    """Opt-in ``usage_limit`` failure category and per-agent cooldown
    (top-level ``usage_limits`` key; absent or ``enabled: false`` = off)."""

    use_default_patterns: bool = True
    patterns: tuple[str, ...] = ()
    agent_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    default_cooldown_seconds: float = 3600.0
    max_cooldown_seconds: float = 86400.0

    def patterns_for(self, agent: str | None) -> tuple[str, ...]:
        from .failure import DEFAULT_USAGE_LIMIT_PATTERNS

        combined = (DEFAULT_USAGE_LIMIT_PATTERNS if self.use_default_patterns else ()) + self.patterns
        if agent:
            combined += self.agent_patterns.get(agent, ())
        return tuple(dict.fromkeys(item.lower() for item in combined if item.strip()))


@dataclass(frozen=True)
class AppConfig:
    agents: dict[str, AgentConfig]
    role_preferences: dict[str, tuple[str, ...]]
    verification_commands: tuple[tuple[str, ...], ...] = ()
    max_attempts: int = 2
    concurrency: int = 1
    max_repair_cycles: int = 1
    pass_env_names: tuple[str, ...] = ()
    pass_env_prefixes: tuple[str, ...] = ()
    verification_profiles: dict[str, VerificationProfile] = field(default_factory=dict)
    default_verification_profile: str | None = None
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    backoff_factor: float = 2.0
    routing_enabled: bool = True
    systemd_run_enabled: bool = False
    security_review: bool = False
    max_review_rounds: int = 2
    # Opt-in; None keeps routing and failure classification exactly as before.
    headroom: HeadroomConfig | None = None
    usage_limits: UsageLimitConfig | None = None


# Bundled as package data by AgentOps.spec, so it resolves identically from a
# source checkout, an installed wheel, and the frozen executable.
DEFAULT_CONFIG_PATH = resource_path("agents", "agents.yaml")


def _default_config_text() -> str:
    """Read the default config text, works for wheel, source, and frozen."""
    import sys
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).joinpath("agents", "agents.yaml").read_text(encoding="utf-8")
    # Use importlib.resources for installed wheel / source checkout
    try:
        from importlib import resources
        return resources.files("agentops.agents").joinpath("agents.yaml").read_text(encoding="utf-8")
    except (ModuleNotFoundError, AttributeError, FileNotFoundError):
        # Fallback to filesystem path
        return DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")


def _load_data(path: Path | None = None) -> dict[str, Any]:
    text = _default_config_text() if path is None else path.read_text(encoding="utf-8")
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
    data = _load_data(Path(path) if path else None)
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
        prompt_transport = raw.get("prompt_transport", "auto")
        from .prompt_transport import PROMPT_TRANSPORTS
        if not isinstance(prompt_transport, str) or prompt_transport not in PROMPT_TRANSPORTS:
            raise ValueError(
                f"agents.{name}.prompt_transport must be one of: {', '.join(sorted(PROMPT_TRANSPORTS))}.")
        stdin_args = raw.get("stdin_args")
        if stdin_args is not None:
            if not isinstance(stdin_args, list) or not all(isinstance(item, str) for item in stdin_args):
                raise ValueError(f"agents.{name}.stdin_args must be a list of strings.")
            if any("{prompt}" in item or "{prompt_file}" in item for item in stdin_args):
                raise ValueError(f"agents.{name}.stdin_args must not contain a prompt placeholder.")
            stdin_args = tuple(stdin_args)
        has_prompt = any("{prompt}" in item for item in args)
        has_prompt_file = any("{prompt_file}" in item for item in args)
        if prompt_transport == "file" and not has_prompt_file:
            raise ValueError(f"agents.{name}.prompt_transport 'file' requires a '{{prompt_file}}' placeholder in args.")
        if prompt_transport == "file" and has_prompt:
            raise ValueError(f"agents.{name}.prompt_transport 'file' must not also pass '{{prompt}}' in args.")
        if prompt_transport == "stdin" and stdin_args is None and (has_prompt or has_prompt_file):
            raise ValueError(
                f"agents.{name}.prompt_transport 'stdin' needs stdin_args (args carries a prompt placeholder).")
        if prompt_transport == "argv" and not has_prompt:
            raise ValueError(f"agents.{name}.prompt_transport 'argv' requires a '{{prompt}}' placeholder in args.")
        if not has_prompt and not has_prompt_file and prompt_transport != "stdin":
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
        # Normalize: strip whitespace (so " streaming " matches STREAMING)
        # and drop duplicates (harmless but polluting) — order preserved.
        capabilities = tuple(dict.fromkeys(item.strip() for item in capabilities))
        # Local import: agent_adapter owns the Capability vocabulary but
        # wraps AgentConfig, so a module-level import here would cycle.
        from .agent_adapter import KNOWN_CAPABILITIES
        for item in capabilities:
            if item not in KNOWN_CAPABILITIES:
                warnings.warn(
                    f"agents.{name}.capabilities has unrecognized capability '{item}'; "
                    "it will be passed through as-is.", stacklevel=2)
        display_name = raw.get("display_name")
        if display_name is not None and (not isinstance(display_name, str) or not display_name.strip()):
            raise ValueError(f"agents.{name}.display_name must be a non-empty string when provided.")
        priority = raw.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError(f"agents.{name}.priority must be an integer.")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"agents.{name}.metadata must be a mapping.")
        version_command = raw.get("version_command")
        if version_command is not None:
            if (not isinstance(version_command, list) or not version_command or
                    not all(isinstance(item, str) and item for item in version_command)):
                raise ValueError(f"agents.{name}.version_command must be a non-empty list of strings.")
            version_command = tuple(version_command)
        boolean_fields: dict[str, object] = {}
        for field_name in (
            "structured_output", "cancellation_support", "timeout_support",
            "interactive_support", "noninteractive_support",
        ):
            value = raw.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"agents.{name}.{field_name} must be a boolean when provided.")
            boolean_fields[field_name] = value
        agents[name] = AgentConfig(
            name, command, tuple(args), tuple(roles), timeout, enabled, model,
            capabilities, display_name, priority, metadata, version_command,
            boolean_fields["structured_output"], boolean_fields["cancellation_support"],
            boolean_fields["timeout_support"], boolean_fields["interactive_support"],
            boolean_fields["noninteractive_support"],
            prompt_transport, stdin_args,
        )
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
    routing_setting = runtime.get("routing", {})
    routing_enabled = runtime.get(
        "routing_enabled",
        routing_setting.get("enabled", True) if isinstance(routing_setting, dict) else True,
    )
    # Missing key means off: a config written before this option existed must
    # not start failing every spawn on Linux hosts without systemd --user
    # (containers, WSL, CI).  The bundled agents.yaml leaves it off (U2);
    # examples/hacir-fleet.yaml opts in explicitly.
    systemd_run_enabled = runtime.get("systemd_run_enabled", False)
    if not isinstance(systemd_run_enabled, bool):
        raise ValueError("runtime.systemd_run_enabled must be a boolean.")
    if not isinstance(routing_enabled, bool):
        raise ValueError("runtime.routing_enabled must be a boolean.")
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
    workflows = data.get("workflows", {})
    if not isinstance(workflows, dict):
        raise ValueError("workflows must be a mapping.")
    security_review = workflows.get("security_review", False)
    if not isinstance(security_review, bool):
        raise ValueError("workflows.security_review must be a boolean.")
    max_review_rounds = workflows.get("max_review_rounds", 2)
    if isinstance(max_review_rounds, bool) or not isinstance(max_review_rounds, int) or max_review_rounds < 0:
        raise ValueError("workflows.max_review_rounds must be zero or greater.")
    headroom = _headroom_config(data.get("headroom"), agents, Path(path) if path else None)
    usage_limits = _usage_limit_config(data.get("usage_limits"), agents)
    if (headroom is not None or usage_limits is not None) and not routing_enabled:
        warnings.warn(
            "headroom / usage_limits cooldowns act through the router; with routing disabled "
            "they will not affect agent selection.", stacklevel=2)
    return AppConfig(agents, role_preferences, verification_commands, max_attempts, concurrency,
                     max_repair_cycles, tuple(names), tuple(prefixes),
                     verification_profiles, default_profile,
                     float(backoff_base), float(backoff_max), float(backoff_factor),
                     routing_enabled, systemd_run_enabled,
                     security_review, max_review_rounds,
                     headroom=headroom, usage_limits=usage_limits)


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be a positive number.")
    return float(value)


def _percent(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1000:
        raise ValueError(f"{label} must be a number above 0.")
    return float(value)


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings.")
    return tuple(item.strip() for item in value)


def _headroom_config(raw: Any, agents: dict[str, AgentConfig], config_path: Path | None) -> HeadroomConfig | None:
    """Parse the opt-in ``headroom`` section; absent means no gate."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("headroom must be a mapping.")
    if raw.get("enabled", True) is False:
        return None
    if not isinstance(raw.get("enabled", True), bool):
        raise ValueError("headroom.enabled must be a boolean.")
    file_value = raw.get("file")
    command_value = raw.get("command")
    if (file_value is None) == (command_value is None):
        raise ValueError("headroom needs exactly one of 'file' or 'command'.")
    file_path: Path | None = None
    command: tuple[str, ...] | None = None
    if file_value is not None:
        if not isinstance(file_value, str) or not file_value.strip():
            raise ValueError("headroom.file must be a non-empty string.")
        file_path = Path(os.path.expanduser(os.path.expandvars(file_value.strip())))
        if not file_path.is_absolute() and config_path is not None:
            file_path = config_path.parent / file_path
    else:
        command = _command(command_value, "headroom.command")
    raw_agents = raw.get("agents")
    if not isinstance(raw_agents, dict) or not raw_agents:
        raise ValueError("headroom.agents must map agent names to a tracker provider.")
    bindings: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    for name, binding in raw_agents.items():
        if name not in agents:
            raise ValueError(f"headroom.agents references unknown agent: {name}")
        if isinstance(binding, str) and binding.strip():
            bindings[name] = (binding.strip(), (), ())
            continue
        if not isinstance(binding, dict):
            raise ValueError(f"headroom.agents.{name} must be a provider name or a mapping.")
        provider = binding.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError(f"headroom.agents.{name}.provider must be a non-empty string.")
        windows = _string_list(binding.get("windows", []), f"headroom.agents.{name}.windows")
        limit_ids = _string_list(binding.get("limit_ids", []), f"headroom.agents.{name}.limit_ids")
        bindings[name] = (provider.strip(), windows, limit_ids)
    unknown_policy = raw.get("unknown_policy", "deprioritize")
    if unknown_policy not in ("allow", "deprioritize", "reject"):
        raise ValueError("headroom.unknown_policy must be 'allow', 'deprioritize' or 'reject'.")
    deprioritize_at = raw.get("deprioritize_at_percent", 80.0)
    reject_at = _percent(raw.get("reject_at_percent", 95.0), "headroom.reject_at_percent")
    if deprioritize_at is not None:
        deprioritize_at = _percent(deprioritize_at, "headroom.deprioritize_at_percent")
        if deprioritize_at > reject_at:
            raise ValueError("headroom.deprioritize_at_percent must not exceed reject_at_percent.")
    return HeadroomConfig(
        agents=bindings,
        file=file_path,
        command=command,
        command_timeout_seconds=_positive_number(
            raw.get("command_timeout_seconds", 30.0), "headroom.command_timeout_seconds"),
        cache_seconds=_positive_number(raw.get("cache_seconds", 60.0), "headroom.cache_seconds"),
        max_age_seconds=_positive_number(raw.get("max_age_seconds", 900.0), "headroom.max_age_seconds"),
        reject_at_percent=reject_at,
        deprioritize_at_percent=deprioritize_at,
        unknown_policy=unknown_policy,
    )


def _usage_limit_config(raw: Any, agents: dict[str, AgentConfig]) -> UsageLimitConfig | None:
    """Parse the opt-in ``usage_limits`` section; absent means the category is off."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("usage_limits must be a mapping.")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("usage_limits.enabled must be a boolean.")
    if not enabled:
        return None
    use_defaults = raw.get("use_default_patterns", True)
    if not isinstance(use_defaults, bool):
        raise ValueError("usage_limits.use_default_patterns must be a boolean.")
    patterns = _string_list(raw.get("patterns", []), "usage_limits.patterns")
    raw_agents = raw.get("agents", {})
    if not isinstance(raw_agents, dict):
        raise ValueError("usage_limits.agents must map agent names to pattern lists.")
    agent_patterns: dict[str, tuple[str, ...]] = {}
    for name, value in raw_agents.items():
        if name not in agents:
            raise ValueError(f"usage_limits.agents references unknown agent: {name}")
        agent_patterns[name] = _string_list(value, f"usage_limits.agents.{name}")
    if not use_defaults and not patterns and not agent_patterns:
        raise ValueError("usage_limits needs patterns when use_default_patterns is false.")
    default_cooldown = _positive_number(
        raw.get("default_cooldown_seconds", 3600.0), "usage_limits.default_cooldown_seconds")
    max_cooldown = _positive_number(
        raw.get("max_cooldown_seconds", 86400.0), "usage_limits.max_cooldown_seconds")
    if default_cooldown > max_cooldown:
        raise ValueError("usage_limits.default_cooldown_seconds must not exceed max_cooldown_seconds.")
    return UsageLimitConfig(use_defaults, patterns, agent_patterns, default_cooldown, max_cooldown)


# --- Config precedence (upgrade U2) ---------------------------------------
#
# --config  >  <repo>/.agentops/config.yaml  >  user config  >  bundled.
#
# The repo config sits under ``.agentops/``, which AgentOps already keeps out
# of Git (``.git/info/exclude``), so ``agentops init`` never dirties the base
# worktree the merge gate checks.  The user config is the place for a personal
# agent fleet (see ``examples/hacir-fleet.yaml``).

REPO_CONFIG_RELATIVE = Path(".agentops") / "config.yaml"


@dataclass(frozen=True)
class ConfigSource:
    """Which configuration file wins, and why (``path`` None means bundled)."""

    path: Path | None
    source: str  # "flag" | "repo" | "user" | "bundled"
    repository_root: Path | None = None

    def describe(self) -> str:
        if self.path is None:
            return "bundled defaults (no --config, repo config, or user config found)"
        labels = {"flag": "--config", "repo": "repository config", "user": "user config"}
        return f"{labels.get(self.source, self.source)}: {self.path}"


def find_repository_root(start: str | Path) -> Path | None:
    """Nearest ancestor of ``start`` holding a ``.git`` entry (dir or worktree file).

    Pure filesystem walk, no subprocess: config resolution must work before Git
    is known to be installed (``doctor`` reports that separately).
    """
    try:
        current = Path(start).resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def user_config_path(
    platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Per-user config location: %APPDATA%\\AgentOps\\agents.yaml on Windows,
    $XDG_CONFIG_HOME/agentops/agents.yaml (default ~/.config) elsewhere."""
    platform = platform or sys.platform
    environ = dict(os.environ) if environ is None else environ
    if platform == "win32":
        appdata = environ.get("APPDATA")
        return Path(appdata) / "AgentOps" / "agents.yaml" if appdata else None
    xdg = environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "agentops" / "agents.yaml"
    try:
        base = home if home is not None else Path.home()
    except (RuntimeError, KeyError):
        return None
    return base / ".config" / "agentops" / "agents.yaml"


def resolve_config_source(
    explicit: str | Path | None,
    cwd: str | Path | None = None,
    *,
    platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> ConfigSource:
    """Apply the precedence rule; never reads or validates the chosen file."""
    root = find_repository_root(cwd if cwd is not None else Path.cwd())
    if explicit:
        return ConfigSource(Path(explicit), "flag", root)
    if root is not None:
        repo_config = root / REPO_CONFIG_RELATIVE
        if repo_config.is_file():
            return ConfigSource(repo_config, "repo", root)
    user = user_config_path(platform, environ, home)
    if user is not None and user.is_file():
        return ConfigSource(user, "user", root)
    return ConfigSource(None, "bundled", root)
