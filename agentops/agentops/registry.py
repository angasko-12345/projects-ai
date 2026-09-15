"""Local coding-agent CLI discovery and role-based selection."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .agent_adapter import Capability, CliAdapter, adapter_for
from .config import AgentConfig, AppConfig


@dataclass(frozen=True)
class DetectedAgent:
    config: AgentConfig
    available: bool
    executable: str | None


class AgentRegistry:
    def __init__(self, config: AppConfig):
        self.config = config
        self._detected: dict[str, DetectedAgent] | None = None
        self._adapters: dict[str, CliAdapter] | None = None

    def detect(self, refresh: bool = False) -> dict[str, DetectedAgent]:
        if self._detected is not None and not refresh:
            return self._detected
        self._detected = {
            name: DetectedAgent(agent, (executable := shutil.which(agent.command)) is not None, executable)
            if agent.enabled else DetectedAgent(agent, False, None)
            for name, agent in self.config.agents.items()
        }
        return self._detected

    def get(self, name: str) -> DetectedAgent:
        try:
            return self.detect()[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

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
        adapters = self.adapters() if required_capabilities else {}
        for name in ordered:
            if name not in detected:
                continue
            candidate = detected[name]
            if candidate.available and name not in excluded and (not candidate.config.roles or role in candidate.config.roles):
                if required_capabilities:
                    # Every name here passed `name in detected`, and adapters
                    # covers every configured agent, so the lookup is total.
                    adapter = adapters[name]
                    if not all(adapter.matches(item) for item in required_capabilities):
                        continue
                return candidate
        return None
