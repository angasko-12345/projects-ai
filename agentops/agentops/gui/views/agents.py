"""Agents surface: detected/configured agent profiles with capability detail."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from ..detail import AgentDetailPanel
from ..format import elide
from ..widgets import TableColumn, ghost_button
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("display_name", "Agent", 170),
    TableColumn("availability", "Availability", 110,
                format=lambda value: "available" if value else "unavailable"),
    TableColumn("configured_priority", "Priority", 80,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else 1e9),
    TableColumn("detected_version", "Version", 110,
                format=lambda value: str(value or "") or "—"),
    TableColumn(
        "metadata", "Model", 130,
        format=lambda value: str((value or {}).get("model") or "") or "—"
        if isinstance(value, dict) else "—",
    ),
    TableColumn("roles", "Roles", 200,
                format=lambda value: ", ".join(str(v) for v in value or [])),
    TableColumn("capabilities", "Capabilities", 240,
                format=lambda value: elide(
                    ", ".join(str(v) for v in value or []), 48)),
    TableColumn("cancellation_support", "Cancel", 80,
                format=lambda value: "yes" if value else "no"),
    TableColumn("timeout_support", "Timeout", 80,
                format=lambda value: "yes" if value else "no"),
    TableColumn("executable_path", "Executable", 260,
                format=lambda value: elide(str(value or ""), 64) or "—"),
)


class AgentsView(ListDetailView):
    """Agent profiles; selecting one shows full capability detail."""

    view_id = "agents"
    title = "Agents"
    page_subtitle = "Detected and configured coding-agent CLIs"
    columns = _COLUMNS
    empty_heading = "No agents detected"
    empty_detail = "Configure agents in agentops.yaml and re-detect."
    search_placeholder = "Search agents..."

    # ------------------------------------------------------------------
    def _build_detail(self) -> AgentDetailPanel:
        return AgentDetailPanel(self.ctx)

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        redetect = ghost_button("Re-detect agents")
        redetect.clicked.connect(self._redetect)
        toolbar.addWidget(redetect)

    # ------------------------------------------------------------------
    def _load(self) -> list[dict]:
        controller = self.ctx.controller
        profiles = list(controller.list_agent_profiles())
        for profile in profiles:
            profile["id"] = str(profile.get("identifier") or profile.get("display_name") or "")
        return profiles

    def _redetect(self) -> None:
        controller = self.ctx.controller

        def done(result: object) -> None:
            detected = len(result.get("profiles", [])) if isinstance(result, dict) else 0
            self.ctx.toast("Detection finished",
                           f"{detected} agent(s) found.", "success")
            self.refresh()

        self.submit("detect", controller.detect_agents, done)

    def fill_detail(self, row: dict | None) -> None:
        self._detail.set_agent(row)
