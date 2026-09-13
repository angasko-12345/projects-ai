"""Local coding-agent CLI discovery and role-based selection."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

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

    def detect(self, refresh: bool = False) -> dict[str, DetectedAgent]:
        if self._detected is not None and not refresh:
            return self._detected
        self._detected = {
            name: DetectedAgent(agent, (executable := shutil.which(agent.command)) is not None, executable)
            for name, agent in self.config.agents.items()
        }
        return self._detected

    def get(self, name: str) -> DetectedAgent:
        try:
            return self.detect()[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent: {name}") from error

    def select(self, role: str, excluded: set[str] | None = None) -> DetectedAgent | None:
        excluded = excluded or set()
        detected = self.detect()
        preferred = self.config.role_preferences.get(role, ())
        ordered = list(preferred) + [name for name in detected if name not in preferred]
        for name in ordered:
            candidate = detected[name]
            if candidate.available and name not in excluded and (not candidate.config.roles or role in candidate.config.roles):
                return candidate
        return None
