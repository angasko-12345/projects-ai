"""Agents surface: detected/configured agent profiles with capability detail."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from ..format import elide
from ..widgets import Card, KeyValueGrid, TableColumn, faint, ghost_button
from .listdetail import ListDetailView

_COLUMNS = (
    TableColumn("display_name", "Agent", 170),
    TableColumn("availability", "Availability", 110,
                format=lambda value: "available" if value else "unavailable"),
    TableColumn("configured_priority", "Priority", 80,
                sort=lambda value: float(value) if isinstance(value, (int, float)) else 1e9),
    TableColumn("detected_version", "Version", 110),
    TableColumn(
        "metadata", "Model", 130,
        format=lambda value: str((value or {}).get("model") or "")
        if isinstance(value, dict) else "",
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
                format=lambda value: elide(str(value or ""), 64)),
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
    def _build_detail(self) -> Card:
        card = Card("Agent detail")
        self._grid = KeyValueGrid(columns=2)
        card.add(self._grid)
        self._detail_placeholder = faint(
            "Pick an agent to inspect its configuration.")
        card.add(self._detail_placeholder)
        return card  # type: ignore[return-value]

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
        grid = self._grid
        grid.clear()
        if not row:
            self._detail_placeholder.show()
            return
        self._detail_placeholder.hide()
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        grid.add_row("Display name", row.get("display_name"))
        grid.add_row("Identifier", row.get("identifier"), mono_value=True)
        grid.add_row("Executable", row.get("executable_path"), mono_value=True)
        grid.add_row("Detected version", row.get("detected_version"))
        grid.add_row("Availability",
                     "available" if row.get("availability") else "unavailable")
        grid.add_row("Priority", row.get("configured_priority"))
        grid.add_row("Model", metadata.get("model"))
        grid.add_row("Roles", ", ".join(str(v) for v in row.get("roles") or []))
        grid.add_row("Capabilities",
                     ", ".join(str(v) for v in row.get("capabilities") or []))
        grid.add_row("Structured output",
                     "yes" if row.get("supported_structured_output") else "no")
        grid.add_row("Cancellation",
                     "yes" if row.get("cancellation_support") else "no")
        grid.add_row("Timeout", "yes" if row.get("timeout_support") else "no")
        grid.add_row("Interactive",
                     "yes" if row.get("interactive_support") else "no")
        grid.add_row("Non-interactive",
                     "yes" if row.get("noninteractive_support") else "no")
