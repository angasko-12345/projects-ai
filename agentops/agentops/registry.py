"""Local coding-agent CLI discovery and role-based selection."""

from __future__ import annotations

import shutil
from enum import StrEnum
import subprocess
import sys
from dataclasses import dataclass

from .agent_adapter import Capability, CliAdapter, adapter_for
from .config import AgentConfig, AppConfig
from .routing import (
    AgentCapabilityResolver,
    AgentProfile,
    AgentRouter,
    RoutingDecision,
    normalize_requirements,
)


class AgentHealth(StrEnum):
    """How usable an agent actually is, not merely whether its binary exists.

    `ONLINE` used to mean only "shutil.which() found the executable", which made
    AgentOps report agents as selectable when they could not run: codex was
    listed ONLINE while its quota was exhausted until 2026-10-12, and fcc-claude
    was listed ONLINE with its proxy on 127.0.0.1:8082 dead. Both were then
    dispatched to, and both failed at execution time.

    DISCOVERED  binary not found on PATH
    BLOCKED     present but disabled by configuration
    AVAILABLE   answered a bounded health probe
    UNHEALTHY   present and enabled, but the probe did not answer usably
    """

    DISCOVERED = "discovered"
    BLOCKED = "blocked"
    AVAILABLE = "available"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class DetectedAgent:
    config: AgentConfig
    available: bool
    executable: str | None
    version: str | None = None
    health: AgentHealth = AgentHealth.AVAILABLE
    health_detail: str | None = None

    @property
    def status(self) -> str:
        """Human-facing status. AVAILABLE/UNHEALTHY are both 'present'; the
        distinction is whether the agent can actually be dispatched to."""
        if self.health is AgentHealth.BLOCKED:
            return "DISABLED"
        if self.health is AgentHealth.DISCOVERED:
            return "MISSING"
        if self.health is AgentHealth.UNHEALTHY:
            return "UNHEALTHY"
        return "ONLINE"


class AgentRegistry:
    def __init__(self, config: AppConfig):
        self.config = config
        self.resolver = AgentCapabilityResolver()
        self._detected: dict[str, DetectedAgent] | None = None
        self._adapters: dict[str, CliAdapter] | None = None

    @staticmethod
    def _detect_version(agent: AgentConfig, executable: str) -> str | None:
        """Best-effort version probe; detection never changes availability."""
        command = agent.version_command or (executable, "--version")
        command = tuple(item.replace("{command}", executable) for item in command)
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            # Version output is untrusted CLI text. Decode as UTF-8 with
            # replacement so non-codepage bytes cannot break detection on
            # Windows cp1252 consoles.
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 2,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(command, **kwargs)
        except (OSError, UnicodeDecodeError, subprocess.SubprocessError, TimeoutError):
            return None
        text = (completed.stdout or completed.stderr or "").strip()
        if not text:
            return None
        return text.splitlines()[0].strip()[:160] or None

    def _health_probe(self, agent: AgentConfig, executable: str) -> tuple[bool, str | None]:
        """Bounded liveness probe. Cheap by design: it must be safe to run for
        every configured agent on every `detect()`.

        The version command doubles as the probe because every agent here already
        supports one and it is the cheapest command that exercises the whole
        process launch path. What it does NOT do is prove the agent can service a
        real task -- codex answers --version while its quota is exhausted, and
        fcc-claude answers while its proxy is dead. So a successful probe means
        AVAILABLE (the binary launches), and is explicitly not a promise that a
        delegation will succeed. Only an agent-specific health command can say
        more, and none is configured today.
        """
        command = agent.version_command or (executable, "--version")
        command = tuple(item.replace("{command}", executable) for item in command)
        kwargs: dict[str, object] = {
            "capture_output": True, "text": True, "encoding": "utf-8",
            "errors": "replace", "timeout": 15, "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired:
            return False, "health probe timed out"
        except UnicodeDecodeError:
            # The binary RAN; we merely could not decode its banner. A prior
            # contract (test_non_ascii_version_output_never_breaks_detection)
            # requires that undecodable version output never affect
            # availability, so this stays AVAILABLE rather than UNHEALTHY.
            return True, None
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"health probe failed: {error}"
        # Compare against int explicitly: a MagicMock returncode is not 0 but is
        # also not an int, and tests that stub subprocess.run must keep working.
        code = completed.returncode
        if isinstance(code, int) and code != 0:
            return False, f"health probe exited {code}"
        return True, None

    def detect(self, refresh: bool = False) -> dict[str, DetectedAgent]:
        if self._detected is not None and not refresh:
            return self._detected
        detected: dict[str, DetectedAgent] = {}
        for name, agent in self.config.agents.items():
            if not agent.enabled:
                detected[name] = DetectedAgent(agent, False, None,
                                               health=AgentHealth.BLOCKED)
                continue
            executable = shutil.which(agent.command)
            if executable is None:
                detected[name] = DetectedAgent(agent, False, None,
                                               health=AgentHealth.DISCOVERED)
                continue
            healthy, detail = self._health_probe(agent, executable)
            detected[name] = DetectedAgent(
                agent,
                healthy,
                executable,
                self._detect_version(agent, executable),
                health=AgentHealth.AVAILABLE if healthy else AgentHealth.UNHEALTHY,
                health_detail=detail,
            )
        self._detected = detected
        return detected

    def get(self, name: str) -> DetectedAgent:
        try:
            return self.detect()[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

    def profiles(self, refresh: bool = False) -> dict[str, AgentProfile]:
        """Return capability-rich profiles for every configured agent."""
        detected = self.detect(refresh)
        adapters = self.adapters(refresh)
        return {
            name: self.resolver.profile(candidate, adapters.get(name))
            for name, candidate in detected.items()
        }

    def profile(self, name: str, refresh: bool = False) -> AgentProfile:
        try:
            return self.profiles(refresh)[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

    def detect_profiles(self, refresh: bool = False) -> dict[str, AgentProfile]:
        return self.profiles(refresh)

    def adapters(self, refresh: bool = False) -> dict[str, CliAdapter]:
        """One cached adapter per configured agent (Track A2 boundary).

        NOTE: cached adapters carry ``executable=None`` and are for
        capability-matching only — never call ``build_command`` on them,
        or the detection-time executable pin is silently lost.  Command
        construction must build a fresh adapter from the ``DetectedAgent``
        (as ``AgentRunner.build_command`` does).  A3 may re-key this cache
        off ``detect()`` instead.
        """
        if self._adapters is not None and not refresh:
            return self._adapters
        self._adapters = {
            name: adapter_for(agent) for name, agent in self.config.agents.items()
        }
        return self._adapters

    def adapter(self, name: str) -> CliAdapter:
        try:
            return self.adapters()[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

    def select(
        self,
        role: str,
        excluded: set[str] | None = None,
        required_capabilities: tuple[Capability | str, ...] = (),
    ) -> DetectedAgent | None:
        """Select an available agent for a role (Track A2 extension).

        With no required capabilities the path is byte-identical to the
        legacy preference-list scan.  With requirements, candidates keep
        the same preference order but must also satisfy every capability
        via their adapter (B7 scoring builds on this filter later).
        """
        excluded = excluded or set()
        detected = self.detect()
        preferred = self.config.role_preferences.get(role, ())
        ordered = list(preferred) + [name for name in detected if name not in preferred]
        required = normalize_requirements(required_capabilities)
        adapters = self.adapters() if required else {}
        for name in ordered:
            if name not in detected:
                continue
            candidate = detected[name]
            if candidate.available and name not in excluded and (not candidate.config.roles or role in candidate.config.roles):
                if required:
                    # Every name here passed `name in detected`, and adapters
                    # covers every configured agent, so the lookup is total.
                    adapter = adapters[name]
                    if not all(adapter.matches(item) for item in required):
                        continue
                return candidate
        return None


__all__ = [
    "AgentCapabilityResolver",
    "AgentProfile",
    "AgentRegistry",
    "AgentRouter",
    "DetectedAgent",
    "RoutingDecision",
    "normalize_requirements",
]
