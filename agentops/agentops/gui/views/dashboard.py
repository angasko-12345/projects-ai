"""Dashboard: current repository, live counts, health, and activity.

Every number comes from ``dashboard_summary`` (persisted state) plus one
``list_agent_profiles`` read for the agents card - no invented metrics.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..format import elide, format_timestamp, short_id
from ..widgets import (
    Card,
    TablePanel,
    TableColumn,
    ghost_button,
    muted,
)
from .base import BaseView

_WORKFLOW_COLUMNS = (
    TableColumn("status", "Status", 110),
    TableColumn("description", "Description", 240,
                format=lambda value: elide(str(value or ""), 64)),
    TableColumn("task_count", "Tasks", 80),
    TableColumn("run_count", "Runs", 80),
    TableColumn("created_at", "Created", 150, format=format_timestamp),
    TableColumn("id", "Workflow", 100, format=lambda value: short_id(value, 8)),
)


def _counts_line(counts: dict, exclude: str | None = None) -> str:
    parts = [
        f"{value} {key.replace('_', ' ')}"
        for key, value in sorted(counts.items())
        if value and key != exclude
    ]
    return "  ·  ".join(parts) if parts else "none yet"


class DashboardView(BaseView):
    """One-screen overview of the current repository."""

    view_id = "dashboard"
    title = "Dashboard"

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._repo_card = self._stat_card("Repository")
        self._repo_detail = self._card_detail(self._repo_card)
        self._workflows_card = self._stat_card("Active workflows")
        self._workflows_stat = self._card_stat(self._workflows_card)
        self._workflows_detail = self._card_detail(self._workflows_card)
        self._tasks_card = self._stat_card("Current tasks")
        self._tasks_stat = self._card_stat(self._tasks_card)
        self._tasks_detail = self._card_detail(self._tasks_card)
        self._verification_card = self._stat_card("Verification health")
        self._verification_stat = self._card_stat(self._verification_card)
        self._verification_detail = self._card_detail(self._verification_card)
        for card in (self._repo_card, self._workflows_card,
                     self._tasks_card, self._verification_card):
            stats.addWidget(card, stretch=1)
        root.addLayout(stats)

        main = QHBoxLayout()
        main.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(12)
        attention_card = Card("Needs attention")
        attention_body = attention_card.body()
        self._attention_count = muted("")
        attention_body.addWidget(self._attention_count)
        self._attention_empty = muted("Nothing needs attention right now.")
        attention_body.addWidget(self._attention_empty)
        self._attention_list = QListWidget()
        self._attention_list.setMaximumHeight(160)
        attention_body.addWidget(self._attention_list)
        attention_actions = QHBoxLayout()
        attention_actions.addStretch(1)
        open_failures = ghost_button("Open failures")
        open_failures.clicked.connect(lambda: self.ctx.navigate("failures"))
        attention_actions.addWidget(open_failures)
        attention_body.addLayout(attention_actions)
        left.addWidget(attention_card)

        workflows_card = Card("Recent workflows")
        self._workflows_table = TablePanel(
            _WORKFLOW_COLUMNS, "No workflows yet",
            "Start a task to see workflows here."
        )
        workflows_card.add(self._workflows_table)
        self._workflows_table.activated.connect(
            lambda row: self.ctx.open_task(str(row.get("id") or ""), "")
        )
        left.addWidget(workflows_card, stretch=1)
        main.addLayout(left, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(12)
        agents_card = Card("Detected agents")
        self._agents_stat = self._card_stat(agents_card)
        self._agents_detail = self._card_detail(agents_card)
        agents_actions = QHBoxLayout()
        agents_actions.addStretch(1)
        open_agents = ghost_button("Open agents")
        open_agents.clicked.connect(lambda: self.ctx.navigate("agents"))
        agents_actions.addWidget(open_agents)
        agents_card.body().addLayout(agents_actions)
        right.addWidget(agents_card)

        activity_card = Card("Recent activity")
        activity_body = activity_card.body()
        self._activity_empty = muted("No activity recorded yet.")
        activity_body.addWidget(self._activity_empty)
        self._activity_list = QListWidget()
        activity_body.addWidget(self._activity_list)
        right.addWidget(activity_card, stretch=1)
        main.addLayout(right, stretch=2)

        root.addLayout(main, stretch=1)
        self._show_empty("No repository chosen",
                         "Pick a repository from the top bar to begin.")

    # ------------------------------------------------------------------
    def _stat_card(self, title: str) -> Card:
        return Card(title)

    def _card_stat(self, card: Card) -> QLabel:
        stat = QLabel("-")
        stat.setProperty("role", "stat")
        card.body().addWidget(stat)
        return stat

    def _card_detail(self, card: Card) -> QLabel:
        detail = QLabel("")
        detail.setProperty("role", "muted")
        detail.setWordWrap(True)
        card.body().addWidget(detail)
        return detail

    def _show_empty(self, heading: str, message: str) -> None:
        self._repo_detail.setText(f"{heading} - {message}")
        self._workflows_stat.setText("-")
        self._workflows_detail.setText("")
        self._tasks_stat.setText("-")
        self._tasks_detail.setText("")
        self._verification_stat.setText("-")
        self._verification_detail.setText("")
        self._agents_stat.setText("-")
        self._agents_detail.setText("")
        self._attention_list.clear()
        self._attention_count.setText("")
        self._attention_empty.show()
        self._workflows_table.set_rows([])
        self._activity_list.clear()
        self._activity_empty.show()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._show_empty("No repository chosen",
                             "Pick a repository from the top bar to begin.")
            return
        controller = self.ctx.controller
        self.submit(
            "summary",
            lambda: controller.dashboard_summary(
                repository, workflow_limit=8, event_limit=12,
                failure_limit=10, verification_limit=10,
            ),
            self._on_summary,
            lambda message: self._show_empty("State unavailable", message),
        )
        self.submit(
            "agents",
            lambda: controller.list_agent_profiles(),
            self._on_agents,
        )

    def on_operation_event(self, event: dict) -> None:
        if str(event.get("kind") or "") in (
            "workflow-started", "workflow-result", "result", "thread-finished"
        ):
            self.refresh()

    # ------------------------------------------------------------------
    def _on_summary(self, summary: object) -> None:
        if not isinstance(summary, dict):
            return
        self._repo_detail.setText(
            f"{summary.get('repository') or '-'}  ·  "
            f"{summary.get('total_workflows', 0)} workflows"
        )
        active = summary.get("active_workflows") or {}
        running = int(active.get("running") or 0)
        pending = int(active.get("pending") or 0)
        self._workflows_stat.setText(str(running))
        self._workflows_detail.setText(f"{pending} pending")

        task_counts = dict(summary.get("task_counts") or {})
        self._tasks_stat.setText(str(int(task_counts.get("running") or 0)))
        self._tasks_detail.setText(_counts_line(task_counts, exclude="running"))

        verification = dict(summary.get("verification_status") or {})
        self._verification_stat.setText(str(int(verification.get("passed") or 0)))
        self._verification_detail.setText(_counts_line(verification, exclude="passed"))

        needs = list(summary.get("needs_attention") or [])
        self._attention_count.setText(
            f"{len(needs)} need attention" if needs else ""
        )
        self._attention_empty.setVisible(not needs)
        self._attention_list.clear()
        for failure in needs:
            text = (
                f"{str(failure.get('category') or 'unknown').lower()}  ·  "
                f"{str(failure.get('severity') or '').lower()}  -  "
                f"{failure.get('recommended_action') or 'inspect'}"
            )
            item = QListWidgetItem(elide(text, 90))
            item.setToolTip(str(failure.get("primary_error") or text))
            self._attention_list.addItem(item)

        rows = list(summary.get("recent_workflows") or [])
        self._workflows_table.set_rows(rows)

        events = list(summary.get("recent_events") or [])
        self._activity_empty.setVisible(not events)
        self._activity_list.clear()
        for event in events:
            message = str(event.get("message") or event.get("type") or "")
            timestamp = str(event.get("timestamp") or "")
            item = QListWidgetItem(
                elide(f"{format_timestamp(timestamp)}  {message}", 110)
            )
            item.setToolTip(f"{timestamp}\n{message}")
            self._activity_list.addItem(item)

    def _on_agents(self, profiles: object) -> None:
        if not isinstance(profiles, list):
            return
        available = sum(1 for profile in profiles if profile.get("availability"))
        self._agents_stat.setText(f"{available} / {len(profiles)}")
        self._agents_detail.setText("available now")
