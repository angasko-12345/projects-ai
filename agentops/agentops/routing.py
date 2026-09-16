"""Capability-aware agent profiles and deterministic routing.

This module is deliberately independent of SQLite and process execution.  The
registry turns detected CLI configuration into :class:`AgentProfile` values,
while :class:`AgentRouter` scores those profiles and returns an explainable
decision.  Workflow persistence is the caller's responsibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .agent_adapter import Capability, task_capabilities_for_roles
from .config import AgentConfig


_INITIAL_CAPABILITIES: tuple[str, ...] = (
    "planning",
    "architecture",
    "implementation",
    "debugging",
    "refactoring",
    "testing",
    "code review",
    "security review",
    "documentation",
    "repository exploration",
)

# These are task capabilities rather than the older adapter-level transport
# capabilities (coding/review/structured_output/...).  The resolver intentionally
# keeps both vocabularies available so A2 configuration remains compatible.
# Canonical role mapping lives in agent_adapter.task_capabilities_for_roles.

_CAPABILITY_ALIASES: dict[str, str] = {
    "code-review": "code review",
    "code_review": "code review",
    "security-review": "security review",
    "security_review": "security review",
    "repository-exploration": "repository exploration",
    "repository_exploration": "repository exploration",
    "repo exploration": "repository exploration",
    "exploration": "repository exploration",
    "docs": "documentation",
    "tests": "testing",
    "test": "testing",
    "refactor": "refactoring",
    "debug": "debugging",
    "implement": "implementation",
    "reviewer": "code review",
    "plan": "planning",
}

_TASK_CAPABILITY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("security", "vulnerability", "threat"), "security review"),
    (("code review", "review", "pull request"), "code review"),
    (("documentation", "document", "readme", "docs"), "documentation"),
    (("repository", "explore", "investigate", "understand"), "repository exploration"),
    (("refactor", "restructure"), "refactoring"),
    (("debug", "diagnose", "fix"), "debugging"),
    (("test", "tests", "testing", "coverage", "spec"), "testing"),
    (("architecture", "design"), "architecture"),
    (("plan", "planning"), "planning"),
    (("implement", "implementation", "feature", "build"), "implementation"),
)


def _normalise_capability(value: object) -> str:
    """Normalise enum/string capability spellings without raising."""
    if isinstance(value, Capability):
        value = value.value
    text = str(value).strip().lower()
    return _CAPABILITY_ALIASES.get(text, text)


def _optional_bool(value: object | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return default


def _as_tuple_strings(value: object) -> tuple[str, ...]:
    """Normalise one string or an iterable of strings without raising."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        try:
            return tuple(str(item) for item in value)
        except Exception:
            return ()
    return (str(value),)


def normalize_requirements(value: object) -> tuple[str, ...]:
    """Public normaliser for one capability or an iterable of capabilities."""
    return _as_tuple_strings(value)


def _history_score(identifier: str, historical_performance: Mapping[str, object] | None) -> float:
    if not historical_performance:
        return 0.0
    try:
        value = historical_performance.get(identifier)
    except Exception:
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    if not isinstance(value, Mapping):
        return 0.0
    try:
        if "success_rate" in value:
            return max(0.0, min(1.0, float(value["success_rate"])))
        if "score" in value:
            return max(0.0, min(1.0, float(value["score"])))
        successes = float(value.get("successes", 0))
        failures = float(value.get("failures", 0))
        total = successes + failures
        return successes / total if total else 0.0
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True, eq=False)
class AgentProfile:
    """Detected, user-facing description of one configured agent."""

    identifier: str
    display_name: str
    executable_path: str | None
    detected_version: str | None
    availability: bool
    roles: tuple[str, ...]
    capabilities: frozenset[str]
    supported_structured_output: bool
    cancellation_support: bool
    timeout_support: bool
    interactive_support: bool
    noninteractive_support: bool
    configured_priority: int
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", str(self.identifier))
        object.__setattr__(self, "display_name", str(self.display_name or self.identifier))
        object.__setattr__(self, "roles", tuple(dict.fromkeys(str(item) for item in self.roles)))
        object.__setattr__(self, "capabilities", frozenset(_normalise_capability(item) for item in self.capabilities))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def available(self) -> bool:
        return self.availability

    @property
    def name(self) -> str:
        return self.identifier

    @property
    def priority(self) -> int:
        return self.configured_priority

    def supports_role(self, role: str) -> bool:
        return not self.roles or role in self.roles

    def supports_capability(self, capability: object) -> bool:
        return _normalise_capability(capability) in self.capabilities

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "display_name": self.display_name,
            "executable_path": self.executable_path,
            "detected_version": self.detected_version,
            "availability": self.availability,
            "roles": list(self.roles),
            "capabilities": sorted(self.capabilities),
            "supported_structured_output": self.supported_structured_output,
            "cancellation_support": self.cancellation_support,
            "timeout_support": self.timeout_support,
            "interactive_support": self.interactive_support,
            "noninteractive_support": self.noninteractive_support,
            "configured_priority": self.configured_priority,
            "metadata": dict(self.metadata),
        }

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.identifier == other
        if isinstance(other, AgentProfile):
            return self.identifier == other.identifier
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.identifier)

    def __str__(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class RoutingAlternative:
    """One eligible non-selected candidate in a routing decision."""

    identifier: str
    score: float
    reasons: tuple[str, ...]
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "score": self.score,
            "reasons": list(self.reasons),
            "capabilities": sorted(self.capabilities),
        }


@dataclass(frozen=True)
class RoutingRejection:
    """One candidate that could not be selected and why."""

    identifier: str
    reasons: tuple[str, ...]
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "reasons": list(self.reasons),
            "constraints": list(self.constraints),
        }


@dataclass(frozen=True)
class RoutingDecision:
    """Explainable result returned by :class:`AgentRouter`."""

    selected_agent: AgentProfile | None
    score: float
    alternatives: tuple[RoutingAlternative, ...]
    reasons: tuple[str, ...]
    rejected_candidates: tuple[RoutingRejection, ...]
    constraints: tuple[str, ...]
    routing_mode: str = "deterministic"

    @property
    def selected(self) -> AgentProfile | None:
        return self.selected_agent

    @property
    def agent(self) -> AgentProfile | None:
        return self.selected_agent

    @property
    def selected_identifier(self) -> str | None:
        return self.selected_agent.identifier if self.selected_agent is not None else None

    @property
    def selected_agent_id(self) -> str | None:
        return self.selected_identifier

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_agent": self.selected_identifier,
            "selected_profile": self.selected_agent.to_dict() if self.selected_agent is not None else None,
            "score": self.score,
            "alternatives": [item.to_dict() for item in self.alternatives],
            "reasons": list(self.reasons),
            "rejected_candidates": [item.to_dict() for item in self.rejected_candidates],
            "constraints": list(self.constraints),
            "routing_mode": self.routing_mode,
        }

    def __getitem__(self, key: str) -> object:
        """Allow lightweight mapping-style consumers without a dict contract."""
        if key == "selected_agent":
            return self.selected_identifier
        if key == "selected_profile":
            return self.selected_agent
        if key == "score":
            return self.score
        if key == "alternatives":
            return self.alternatives
        if key == "reasons":
            return self.reasons
        if key == "rejected_candidates":
            return self.rejected_candidates
        if key == "constraints":
            return self.constraints
        if key == "routing_mode":
            return self.routing_mode
        raise KeyError(key)


class AgentCapabilityResolver:
    """Derive and match task capabilities for configured agents."""

    def __init__(self, capabilities: Iterable[str] | None = None):
        self.capabilities = frozenset(
            item for item in (_INITIAL_CAPABILITIES if capabilities is None else capabilities)
            if _normalise_capability(item)
        )

    @property
    def all_capabilities(self) -> frozenset[str]:
        return frozenset(self.capabilities)

    @staticmethod
    def normalise(capability: object) -> str:
        return _normalise_capability(capability)

    def for_role(self, role: str) -> frozenset[str]:
        """Capabilities honestly implied by an AgentOps workflow role."""
        canonical = {
            _normalise_capability(item)
            for item in task_capabilities_for_roles((role,) if role else ())
        }
        return frozenset(self.capabilities & canonical)

    def resolve(
        self,
        agent: AgentConfig | object,
        adapter: object | None = None,
    ) -> frozenset[str]:
        """Return the capabilities advertised by one configured agent."""
        config = getattr(agent, "config", agent)
        roles = tuple(getattr(config, "roles", ()) or ())
        if roles:
            resolved = set().union(*(self.for_role(role) for role in roles))
        else:
            # Empty roles retain the registry's historical "supports every
            # role" meaning, so expose the complete initial vocabulary.
            resolved = set(self.capabilities)

        declared = tuple(getattr(config, "capabilities", ()) or ())
        for value in declared:
            resolved.add(_normalise_capability(value))

        if adapter is not None:
            try:
                adapter_capabilities = adapter.capabilities()
                for value in adapter_capabilities:
                    resolved.add(_normalise_capability(value))
                extras = getattr(adapter, "extra_capabilities", ())
                for value in extras:
                    resolved.add(_normalise_capability(value))
            except Exception:
                pass

        metadata = getattr(config, "metadata", {}) or {}
        if isinstance(metadata, Mapping):
            for value in metadata.get("capabilities", ()):
                resolved.add(_normalise_capability(value))

        # Preserve old A2 vocabulary while making its intent useful to the
        # task-level router (coding -> implementation, review -> code review).
        if "coding" in resolved:
            resolved.update({"implementation", "refactoring", "testing"})
        if "planning" in resolved:
            resolved.add("architecture")
        if "review" in resolved:
            resolved.update({"code review", "security review"})

        return frozenset(item for item in resolved if item)

    def profile(self, agent: object, adapter: object | None = None) -> AgentProfile:
        """Build a profile from a DetectedAgent-like object."""
        config = getattr(agent, "config", agent)
        if not isinstance(config, AgentConfig):
            raise TypeError("agent must be an AgentConfig or DetectedAgent-like object")
        capabilities = self.resolve(config, adapter)
        metadata = dict(getattr(config, "metadata", {}) or {})
        model = getattr(config, "model", None)
        if model:
            metadata.setdefault("model", model)
        metadata.setdefault("command", getattr(config, "command", ""))
        metadata.setdefault("enabled", bool(getattr(config, "enabled", True)))
        return AgentProfile(
            identifier=getattr(config, "name", ""),
            display_name=getattr(config, "display_name", None) or getattr(config, "name", ""),
            executable_path=getattr(agent, "executable", None),
            detected_version=getattr(agent, "version", None),
            availability=(
                bool(getattr(agent, "available", False))
                and bool(getattr(config, "enabled", True))
            ),
            roles=tuple(getattr(config, "roles", ()) or ()),
            capabilities=capabilities,
            supported_structured_output=_optional_bool(
                getattr(config, "structured_output", None), True,
            ),
            cancellation_support=_optional_bool(
                getattr(config, "cancellation_support", None), True,
            ),
            timeout_support=_optional_bool(
                getattr(config, "timeout_support", None), True,
            ),
            interactive_support=_optional_bool(
                getattr(config, "interactive_support", None), False,
            ),
            noninteractive_support=_optional_bool(
                getattr(config, "noninteractive_support", None), True,
            ),
            configured_priority=int(getattr(config, "priority", 0) or 0),
            metadata=metadata,
        )

    def required_capabilities(
        self,
        role: str,
        task_description: str = "",
        repository_characteristics: Mapping[str, object] | None = None,
    ) -> tuple[str, ...]:
        """Derive deterministic requirements from role, text, and repository hints."""
        required = set(self.for_role(role))
        text = (task_description or "").casefold()
        for keywords, capability in _TASK_CAPABILITY_KEYWORDS:
            if any(keyword in text for keyword in keywords):
                required.add(capability)
        characteristics = repository_characteristics or {}
        if characteristics.get("security_sensitive"):
            required.add("security review")
        if characteristics.get("requires_tests"):
            required.add("testing")
        if characteristics.get("documentation_required"):
            required.add("documentation")
        if characteristics.get("repository_exploration_required"):
            required.add("repository exploration")
        return tuple(sorted(item for item in required if item))

    def matches(
        self,
        profile_or_capabilities: AgentProfile | Iterable[object],
        required_capabilities: Iterable[object] = (),
    ) -> bool:
        if isinstance(profile_or_capabilities, AgentProfile):
            available = profile_or_capabilities.capabilities
        else:
            available = frozenset(_normalise_capability(item) for item in profile_or_capabilities)
        required = frozenset(_normalise_capability(item) for item in required_capabilities)
        return required <= available

    resolve_capabilities = resolve
    required_for = required_capabilities


class AgentRouter:
    """Deterministic, explainable agent router.

    Capability and role constraints are hard gates.  User preferences are a
    large deterministic score component, followed by historical performance,
    configured priority, and identifier as a stable tie-breaker.
    """

    def __init__(
        self,
        resolver: AgentCapabilityResolver | None = None,
        enabled: bool = True,
    ):
        self.resolver = resolver or AgentCapabilityResolver()
        self.enabled = bool(enabled)

    @staticmethod
    def _coerce_profile(value: object, resolver: AgentCapabilityResolver) -> AgentProfile | None:
        if isinstance(value, AgentProfile):
            return value
        if isinstance(value, Mapping):
            try:
                identifier = str(value.get("identifier") or value.get("name") or "")
                if not identifier:
                    return None
                capabilities = _as_tuple_strings(value.get("capabilities", ()))
                return AgentProfile(
                    identifier=identifier,
                    display_name=str(value.get("display_name") or identifier),
                    executable_path=value.get("executable_path") or value.get("executable"),
                    detected_version=value.get("detected_version") or value.get("version"),
                    availability=_optional_bool(
                        value.get("availability", value.get("available", True)), True,
                    ),
                    roles=tuple(_as_tuple_strings(value.get("roles", ()))),
                    capabilities=frozenset(capabilities or ()),
                    supported_structured_output=_optional_bool(
                        value.get("supported_structured_output", True), True,
                    ),
                    cancellation_support=_optional_bool(
                        value.get("cancellation_support", True), True,
                    ),
                    timeout_support=_optional_bool(value.get("timeout_support", True), True),
                    interactive_support=_optional_bool(value.get("interactive_support", False), False),
                    noninteractive_support=_optional_bool(
                        value.get("noninteractive_support", True), True,
                    ),
                    configured_priority=int(value.get("configured_priority", value.get("priority", 0)) or 0),
                    metadata=dict(value.get("metadata", {}) or {}),
                )
            except (TypeError, ValueError):
                return None
        if hasattr(value, "config"):
            try:
                return resolver.profile(value)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _preference_order(user_preferences: Iterable[object] | Mapping[str, object] | None) -> dict[str, int]:
        if isinstance(user_preferences, Mapping):
            raw = user_preferences.get("preferred_agents", user_preferences.get("agents", ()))
            if isinstance(raw, str):
                raw = (raw,)
            return {str(item): index for index, item in enumerate(raw or ())}
        if isinstance(user_preferences, str):
            return {user_preferences: 0}
        return {
            str(item): index
            for index, item in enumerate(user_preferences or ())
        }

    def _static_decision(
        self,
        profiles: tuple[AgentProfile, ...],
        role: str,
        user_preferences: Iterable[object] | Mapping[str, object] | None,
        constraints: tuple[str, ...],
        excluded: Iterable[str] = (),
    ) -> RoutingDecision:
        preferences = self._preference_order(user_preferences)
        excluded_names = set(_as_tuple_strings(excluded))
        eligible: list[tuple[int, AgentProfile]] = []
        rejected: list[RoutingRejection] = []
        for profile in profiles:
            if profile.identifier in excluded_names:
                rejected.append(RoutingRejection(profile.identifier, ("excluded after a previous attempt",), constraints))
                continue
            if not profile.availability:
                rejected.append(RoutingRejection(profile.identifier, ("agent unavailable",), constraints))
                continue
            if not profile.supports_role(role):
                rejected.append(RoutingRejection(profile.identifier, (f"role {role} unsupported",), constraints))
                continue
            order = preferences.get(profile.identifier, len(preferences))
            eligible.append((order, profile))
        eligible.sort(key=lambda item: (item[0], item[1].configured_priority, item[1].identifier))
        selected = eligible[0][1] if eligible else None
        reasons = ["routing disabled; static preference fallback"]
        if selected is not None:
            if selected.identifier in preferences:
                reasons.append("preferred agent")
            else:
                reasons.append("fallback agent")
            reasons.append("available")
        alternatives = tuple(
            RoutingAlternative(item.identifier, 0.0, ("static fallback candidate",), item.capabilities)
            for _, item in eligible[1:]
        )
        return RoutingDecision(selected, 0.0, alternatives, tuple(reasons), tuple(rejected), constraints, "static")

    def route(
        self,
        role: str,
        task_description: str,
        repository_characteristics: Mapping[str, object] | None = None,
        required_capabilities: Iterable[object] = (),
        available_agents: Iterable[object] | None = None,
        user_preferences: Iterable[object] | Mapping[str, object] | None = None,
        historical_performance: Mapping[str, object] | None = None,
        excluded: Iterable[str] = (),
        allow_capability_fallback: bool = False,
    ) -> RoutingDecision:
        """Score available profiles and return an explainable decision.

        Explicit ``required_capabilities`` are hard constraints.  Capabilities
        inferred from the role, task description, and repository hints are
        scoring signals: they influence ranking and reasons, but they never
        disqualify an otherwise eligible candidate.
        """
        if isinstance(available_agents, Mapping):
            available_values = tuple(available_agents.values())
        elif isinstance(available_agents, AgentProfile):
            available_values = (available_agents,)
        else:
            available_values = tuple((available_agents or ()) if not isinstance(available_agents, str) else (available_agents,))
        profiles = tuple(
            profile
            for item in available_values
            if (profile := self._coerce_profile(item, self.resolver)) is not None
        )
        requested = set(self.resolver.normalise(item) for item in _as_tuple_strings(required_capabilities))
        desired = set(requested)
        desired.update(self.resolver.required_capabilities(
            str(role or ""),
            task_description if isinstance(task_description, str) else "",
            repository_characteristics,
        ))
        constraints = (
            f"role: {role}",
            "required capabilities: " + (", ".join(sorted(requested)) if requested else "none"),
            "desired capabilities: " + (", ".join(sorted(desired)) if desired else "none"),
            "routing: " + ("deterministic" if self.enabled else "disabled"),
        )
        if not self.enabled:
            return self._static_decision(profiles, str(role or ""), user_preferences, constraints, excluded)

        preferences = self._preference_order(user_preferences)
        excluded_names = set(_as_tuple_strings(excluded))
        scored: list[tuple[float, int, int, str, AgentProfile, tuple[str, ...]]] = []
        rejected: list[RoutingRejection] = []

        for profile in profiles:
            reasons: list[str] = []
            rejection_reasons: list[str] = []
            if profile.identifier in excluded_names:
                rejection_reasons.append("excluded after a previous attempt")
            if not profile.availability:
                rejection_reasons.append(
                    "agent disabled" if profile.metadata.get("enabled") is False
                    else "agent unavailable"
                )
            if not profile.supports_role(role):
                rejection_reasons.append(f"role {role} unsupported")
            missing = sorted(requested - profile.capabilities)
            if missing:
                rejection_reasons.append("missing required capabilities: " + ", ".join(missing))
            if rejection_reasons:
                rejected.append(RoutingRejection(profile.identifier, tuple(rejection_reasons), constraints))
                continue

            score = 20.0
            reasons.append(f"role {role} supported")
            explicit_matches = sorted(requested & profile.capabilities)
            score += 10.0 * len(explicit_matches)
            for capability in explicit_matches:
                reasons.append(f"{capability} capability matched")
            inferred_matches = sorted((desired - requested) & profile.capabilities)
            score += 5.0 * len(inferred_matches)
            for capability in inferred_matches:
                reasons.append(f"task fit: {capability}")
            if profile.identifier in preferences:
                # A user override is intentionally stronger than historical
                # performance and configured priority, but still respects all
                # hard capability/availability gates.
                score += 1000.0 + (len(preferences) - preferences[profile.identifier]) * 10.0
                reasons.append("preferred agent")
            score -= float(profile.configured_priority)
            performance = _history_score(profile.identifier, historical_performance)
            score += performance * 25.0
            reasons.append("available")
            if not excluded_names:
                reasons.append("fallback not required")
            scored.append((score, preferences.get(profile.identifier, len(preferences)), profile.configured_priority, profile.identifier, profile, tuple(reasons)))

        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        if not scored and allow_capability_fallback:
            # This opt-in path preserves a usable fallback when no candidate
            # meets every inferred capability, while keeping the rejection
            # evidence in the decision.
            for profile in profiles:
                if not profile.availability or not profile.supports_role(role) or profile.identifier in excluded_names:
                    continue
                score = 5.0 - float(profile.configured_priority)
                reasons = ("fallback used; required capabilities unavailable", "available")
                scored.append((score, preferences.get(profile.identifier, len(preferences)), profile.configured_priority, profile.identifier, profile, reasons))
            scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))

        selected = scored[0] if scored else None
        selected_profile = selected[4] if selected else None
        selected_reasons = selected[5] if selected else ("no eligible agent",)
        alternatives = tuple(
            RoutingAlternative(item[3], item[0], item[5], item[4].capabilities)
            for item in scored[1:]
        )
        reasons = tuple(selected_reasons)
        if selected is not None and selected[1] > 0 and selected[4].identifier not in preferences:
            reasons = (*reasons, "fallback used")
        return RoutingDecision(
            selected_profile,
            selected[0] if selected else 0.0,
            alternatives,
            reasons,
            tuple(rejected),
            constraints,
            "deterministic",
        )

    def select(self, **kwargs: object) -> AgentProfile | None:
        """Return only the selected profile for simple callers."""
        return self.route(**kwargs).selected_agent


__all__ = [
    "AgentCapabilityResolver",
    "AgentProfile",
    "AgentRouter",
    "RoutingAlternative",
    "RoutingDecision",
    "RoutingRejection",
    "normalize_requirements",
]
