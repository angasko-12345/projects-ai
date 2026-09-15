"""Agent adapter boundary (Track A, item A2).

Adapters own what is ACTUALLY agent-specific in AgentOps today:

- command/argument shape (which flags carry the prompt),
- executable resolution (detection-time pinning),
- role support (which workflow roles an agent may take),
- capability reporting (what an agent demonstrably provides).

Deliberately OUT of scope for A2 (stays in the runner until A3 builds the
shared ProcessRuntime): environment construction (generic allowlist today,
nothing agent-specific), output parsing (generic JSON scan + ``AgentResult``
coercion, identical for every CLI), cancellation (runner-level tokens and
process groups).  The roadmap's ``build_environment`` / ``parse_output`` /
cancellation hooks belong to adapters only once A3 gives them a runtime to
target — adding them now would invent seams with a single caller.

Honesty rule: baseline capabilities are derived ONLY from configured roles
(implementation/debugging -> coding, architecture -> planning,
review -> review; empty roles list means all three, mirroring the registry's
long-standing empty-means-all gate).  Anything beyond that must be declared
in ``AgentConfig.capabilities`` — and only for behavior the CLI
demonstrably provides.  Unknown names warn at load and pass through as
free-form extras (forward compatibility for B7 scoring).

This module is a leaf: it wraps ``AgentConfig`` (D2 overlay decision) and
imports nothing from registry, runner, workflow, or state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .config import AgentConfig


class Capability(StrEnum):
    """Fixed capability vocabulary (D1). Unknown strings stay free-form extras."""

    CODING = "coding"
    PLANNING = "planning"
    REVIEW = "review"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    MCP = "mcp"
    MODEL_SELECTION = "model_selection"
    READ_ONLY = "read_only"
    NON_INTERACTIVE = "non_interactive"


KNOWN_CAPABILITIES: frozenset[str] = frozenset(item.value for item in Capability)


def capabilities_for_roles(roles: tuple[str, ...]) -> frozenset[Capability]:
    """Honest baseline derived from roles only (empty roles -> all three)."""
    if not roles:
        return frozenset({Capability.CODING, Capability.PLANNING, Capability.REVIEW})
    derived: set[Capability] = set()
    if "implementation" in roles or "debugging" in roles:
        derived.add(Capability.CODING)
    if "architecture" in roles:
        derived.add(Capability.PLANNING)
    if "review" in roles:
        derived.add(Capability.REVIEW)
    return frozenset(derived)


@runtime_checkable
class AgentAdapter(Protocol):
    """Structural contract for agent-specific behavior."""

    @property
    def name(self) -> str: ...

    def capabilities(self) -> frozenset[Capability]: ...

    @property
    def extra_capabilities(self) -> tuple[str, ...]: ...

    def supports(self, role: str) -> bool: ...

    def build_command(self, prompt: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class CliAdapter:
    """Default adapter: thin wrapper around one AgentConfig (D2 overlay).

    Construction and selection behavior are byte-identical to the
    pre-adapter code paths (``AgentRunner.build_command`` and the registry
    role gate); the knowledge just lives behind the boundary now.
    Build via :func:`adapter_for` so declared config capabilities apply.
    """

    config: AgentConfig
    executable: str | None = None
    _extra: tuple[str, ...] = field(default_factory=tuple, repr=False)

    @property
    def name(self) -> str:
        return self.config.name

    def capabilities(self) -> frozenset[Capability]:
        known = {Capability(item) for item in self._extra if item in KNOWN_CAPABILITIES}
        return capabilities_for_roles(self.config.roles) | known

    @property
    def extra_capabilities(self) -> tuple[str, ...]:
        return tuple(item for item in self._extra if item not in KNOWN_CAPABILITIES)

    def supports(self, role: str) -> bool:
        # Exact preservation of the registry's role gate: an empty roles
        # list means the agent takes any role.
        return not self.config.roles or role in self.config.roles

    def build_command(self, prompt: str) -> tuple[str, ...]:
        # Exact preservation of AgentRunner.build_command: prefer the
        # detection-time absolute path so execution cannot pick up a
        # different binary if PATH changes between detection and run
        # (TOCTOU). Users who need a pinned binary can set an absolute
        # path as `command`; shutil.which passes absolute paths through.
        executable = self.executable or self.config.command
        return (executable, *(argument.replace("{prompt}", prompt) for argument in self.config.args))

    def matches(self, required: Capability | str) -> bool:
        """True when one required capability (enum or raw string) is present."""
        if isinstance(required, str) and required in KNOWN_CAPABILITIES:
            required = Capability(required)
        if isinstance(required, Capability):
            return required in self.capabilities()
        return required in self._extra


def adapter_for(config: AgentConfig, executable: str | None = None) -> CliAdapter:
    """Build the adapter for one agent config (declared extras apply)."""
    return CliAdapter(config, executable, tuple(config.capabilities))


__all__ = [
    "KNOWN_CAPABILITIES",
    "AgentAdapter",
    "Capability",
    "CliAdapter",
    "adapter_for",
    "capabilities_for_roles",
]
