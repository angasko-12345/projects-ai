"""Dashboard: current repository, live counts, health, and activity.

Every number comes from ``dashboard_summary`` (persisted state) plus one
``list_agent_profiles`` read for the agents card - no invented metrics.
Cards keep their last good values across refreshes; loading and error text
only shows when nothing has loaded yet.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
)

from ..format import elide, format_timestamp, short_id
from ..tokens import DARK
from ..widgets import (
    Card,
    PageHeader,
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

# Failure severity drives the attention-row foreground; anything unknown
# stays at the default text color so a new severity never renders invisible.
_SEVERITY_COLORS = {
    "critical": DARK.danger,
    "high": DARK.danger,
    "medium": DARK.warning,
    "low": DARK.text_muted,
}

_NO_REPOSITORY = "Pick a repository from the top bar to begin."
_NO_WORKFLOWS = "Start a task to see workflows here."


def _counts_line(counts: dict, exclude: str | None = None) -> str:
    parts = [
        f"{value} {key.replace('_', ' ')}"
        for key, value in sorted(counts.items())
        if value and key != exclude
    ]
    return "  ·  ".join(parts) if parts else "none yet"


def _pretty(text: object) -> str:
    return str(text or "unknown").replace("_", " ")


class DashboardView(BaseView):
    """One-screen overview of the current repository."""

    view_id = "dashboard"
    title = "Dashboard"

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        self._page_header = PageHeader(self.title)
        refresh = ghost_button("Refresh")
        refresh.clicked.connect(self.refresh)
        self._page_header.add_action(refresh)
        root.addWidget(self._page_header)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._workflows_card = self._stat_card("Active workflows")
        self._workflows_stat = self._card_stat(self._workflows_card)
        self._workflows_detail = self._card_detail(self._workflows_card)
        self._tasks_card = self._stat_card("Current tasks")
        self._tasks_stat = self._card_stat(self._tasks_card)
        self._tasks_detail = self._card_detail(self._tasks_card)
        self._verification_card = self._stat_card("Verification health")
        self._verification_stat = self._card_stat(self._verification_card)
        self._verification_detail = self._card_detail(self._verification_card)
        self._agents_card = self._stat_card("Detected agents")
        self._agents_stat = self._card_stat(self._agents_card)
        self._agents_detail = self._card_detail(self._agents_card)
        for card in (self._workflows_card, self._tasks_card,
                     self._verification_card, self._agents_card):
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
        self._attention_empty = muted(_NO_REPOSITORY)
        attention_body.addWidget(self._attention_empty)
        self._attention_list = QListWidget()
        self._attention_list.setMaximumHeight(160)
        self._attention_list.itemActivated.connect(
            lambda _item: self.ctx.navigate("failures"))
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
            _WORKFLOW_COLUMNS, "No workflows yet", _NO_WORKFLOWS
        )
        workflows_card.add(self._workflows_table)
        self._workflows_table.activated.connect(
            lambda row: self.ctx.open_task(str(row.get("id") or ""), ""))
        left.addWidget(workflows_card, stretch=1)
        main.addLayout(left, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(12)
        activity_card = Card("Recent activity")
        activity_body = activity_card.body()
        # The activity card stretches to fill its column; without an
        # Expanding occupant Qt splits the spare height between the card
        # title and the empty label, inflating both. Expanding + centered
        # keeps the title at one line and mirrors the workflows card.
        self._activity_empty = muted(_NO_REPOSITORY)
        self._activity_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._activity_empty.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        activity_body.addWidget(self._activity_empty)
        self._activity_list = QListWidget()
        activity_body.addWidget(self._activity_list)
        right.addWidget(activity_card, stretch=1)
        main.addLayout(right, stretch=2)

        root.addLayout(main, stretch=1)

        self._summary_loaded = False
        self._agents_loaded = False
        self._show_no_repo()

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

    def _show_no_repo(self) -> None:
        """Empty state: no repository chosen, nothing loaded yet."""
        self._summary_loaded = False
        self._agents_loaded = False
        self._page_header.set_subtitle("No repository chosen")
        for stat in (self._workflows_stat, self._tasks_stat,
                     self._verification_stat, self._agents_stat):
            stat.setText("-")
        for detail in (self._workflows_detail, self._tasks_detail,
                       self._verification_detail, self._agents_detail):
            detail.setText("")
        self._attention_count.setText("")
        self._attention_empty.setText(_NO_REPOSITORY)
        self._attention_empty.show()
        self._attention_list.hide()
        self._workflows_table.set_empty_state(
            "No repository chosen", _NO_REPOSITORY)
        self._workflows_table.set_rows([])
        self._activity_empty.setText(_NO_REPOSITORY)
        self._activity_empty.show()
        self._activity_list.hide()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._show_no_repo()
            return
        if not self._summary_loaded:
            for detail in (self._workflows_detail, self._tasks_detail,
                           self._verification_detail):
                detail.setText("loading...")
        if not self._agents_loaded:
            self._agents_detail.setText("detecting...")
        controller = self.ctx.controller
        self.submit(
            "summary",
            lambda: controller.dashboard_summary(
                repository, workflow_limit=8, event_limit=12,
                failure_limit=10, verification_limit=10,
            ),
            self._on_summary,
            self._on_summary_error,
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
    def _on_summary_error(self, message: str) -> None:
        """Error state: say what failed while keeping any loaded numbers."""
        self._page_header.set_subtitle(f"State unavailable - {message}")
        if not self._summary_loaded:
            for detail in (self._workflows_detail, self._tasks_detail,
                           self._verification_detail):
                detail.setText("unavailable")

    def _on_summary(self, summary: object) -> None:
        if not isinstance(summary, dict):
            return
        repository = str(summary.get("repository") or self.repository())
        self._page_header.set_subtitle(
            f"{elide(repository, 48)}  ·  "
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
        if not needs:
            self._attention_empty.setText("Nothing needs attention right now.")
        self._attention_list.setVisible(bool(needs))
        self._attention_list.clear()
        for failure in needs:
            severity = str(failure.get("severity") or "").lower()
            action = _pretty(failure.get("recommended_action"))
            text = elide(
                f"{_pretty(failure.get('category'))}  ·  {severity}  ·  {action}",
                70,
            )
            item = QListWidgetItem(text)
            item.setForeground(QColor(_SEVERITY_COLORS.get(severity, DARK.text)))
            created = str(failure.get("created_at") or "")
            error = str(failure.get("primary_error") or text)
            item.setToolTip(f"{created}\n{error}" if created else error)
            self._attention_list.addItem(item)

        rows = list(summary.get("recent_workflows") or [])
        self._workflows_table.set_empty_state(
            "No workflows yet", _NO_WORKFLOWS)
        self._workflows_table.set_rows(rows)

        events = list(summary.get("recent_events") or [])
        self._activity_empty.setVisible(not events)
        if not events:
            self._activity_empty.setText("No activity recorded yet.")
        self._activity_list.setVisible(bool(events))
        self._activity_list.clear()
        for event in events:
            message = str(event.get("message") or event.get("type") or "")
            timestamp = str(event.get("timestamp") or "")
            item = QListWidgetItem(
                elide(f"{format_timestamp(timestamp)}  {message}", 110)
            )
            item.setToolTip(f"{timestamp}\n{message}")
            self._activity_list.addItem(item)

        self._summary_loaded = True

    def _on_agents(self, profiles: object) -> None:
        if not isinstance(profiles, list):
            return
        available = sum(1 for profile in profiles if profile.get("availability"))
        self._agents_stat.setText(f"{available} / {len(profiles)}")
        self._agents_detail.setText(
            "available now" if profiles else "none detected")
        self._agents_loaded = True
