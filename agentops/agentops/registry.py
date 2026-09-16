"""Local coding-agent CLI discovery and role-based selection."""

from __future__ import annotations

import shutil
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


@dataclass(frozen=True)
class DetectedAgent:
    config: AgentConfig
    available: bool
    executable: str | None
    version: str | None = None


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
            "timeout": 2,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(command, **kwargs)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return None
        text = (completed.stdout or completed.stderr or "").strip()
        if not text:
            return None
        return text.splitlines()[0].strip()[:160] or None

    def detect(self, refresh: bool = False) -> dict[str, DetectedAgent]:
        if self._detected is not None and not refresh:
            return self._detected
        detected: dict[str, DetectedAgent] = {}
        for name, agent in self.config.agents.items():
            executable = shutil.which(agent.command) if agent.enabled else None
            if executable is None:
                detected[name] = DetectedAgent(agent, False, None)
                continue
            detected[name] = DetectedAgent(
                agent,
                True,
                executable,
                self._detect_version(agent, executable),
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
