"""Dashboard: the current repository at a glance.

Every number derives from existing reads: ``dashboard_summary``,
``list_agent_profiles`` for the agents card, and ``list_recent_agent_runs``
(the newest 500) for the runs chart, shaped by the Qt-free ``gui/derive.py``.
Values keep their last good state across refreshes; an empty repository
shows empty states, never zero-filled charts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import derive
from ..charts import AreaChart, Donut
from ..components import Chip, SectionLabel
from ..format import elide, format_timestamp, short_id
from ..tokens import DARK
from ..widgets import (
    Card,
    PageHeader,
    StatCard,
    TablePanel,
    TableColumn,
    ghost_button,
    muted,
)
from .base import BaseView

_WORKFLOW_COLUMNS = (
    TableColumn("status", "Status", 110),
    TableColumn(
        "description", "Description", 240, format=lambda value: elide(str(value or ""), 64)
    ),
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

_NO_REPOSITORY = "Pick a repository in Workflows, or with Ctrl+K, to begin."
_NO_WORKFLOWS = "Start a task to see workflows here."
_READ_LIMIT = 500  # newest agent runs read for the runs chart
_DAYS = 14
_REPO_TITLE_PX = 30
_DECIDED = ("passed", "failed", "timed_out", "unverified")


def _counts_line(counts: dict, exclude: str | None = None) -> str:
    parts = [
        f"{value} {key.replace('_', ' ')}"
        for key, value in sorted(counts.items())
        if value and key != exclude
    ]
    return "  ·  ".join(parts) if parts else "none yet"


def _pretty(text: object) -> str:
    return str(text or "unknown").replace("_", " ")


def _day_label(day) -> str:  # type: ignore[no-untyped-def]
    return f"{day:%b} {day.day}"


def _legend(text: str, color: str) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
    row.addWidget(dot)
    label = QLabel(text)
    label.setProperty("role", "subtitle")
    row.addWidget(label)
    return holder


def _navy_card() -> Card:
    card = Card()
    card.setProperty("navy", True)
    return card


class DashboardView(BaseView):
    """One-screen overview of the current repository."""

    view_id = "dashboard"
    title = "Dashboard"

    # Needs-attention count from each dashboard_summary result, so the shell's
    # rail badge reuses this poll instead of reading state again.
    attention_changed = Signal(int)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        # Scrollable so short windows reach every card instead of clipping
        # the activity feed; the inner layout keeps the usual margins.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(DARK.space_md)

        root.addWidget(self._build_header())

        stats = QHBoxLayout()
        stats.setSpacing(DARK.space_md)
        (self._workflows_card, self._workflows_stat, self._workflows_detail) = self._stat_card(
            "Active workflows", "workflows", "Open the workflow control center"
        )
        (self._tasks_card, self._tasks_stat, self._tasks_detail) = self._stat_card(
            "Current tasks", "tasks", "Open the task list"
        )
        (self._verification_card, self._verification_stat, self._verification_detail) = (
            self._stat_card("Verification health", "verification", "Open verification runs")
        )
        (self._agents_card, self._agents_stat, self._agents_detail) = self._stat_card(
            "Detected agents", "agents", "Open the agent roster"
        )
        for card in (
            self._workflows_card,
            self._tasks_card,
            self._verification_card,
            self._agents_card,
        ):
            stats.addWidget(card, stretch=1)
        root.addLayout(stats)
        root.addLayout(self._build_charts())
        self._window_note = QLabel("")
        self._window_note.setProperty("role", "faint")
        self._window_note.hide()
        root.addWidget(self._window_note)
        self._runs: list[dict] = []

        main = QHBoxLayout()
        main.setSpacing(DARK.space_md)

        left = QVBoxLayout()
        left.setSpacing(DARK.space_md)
        attention_card = Card("Needs attention")
        attention_body = attention_card.body()
        self._attention_count = muted("")
        attention_body.addWidget(self._attention_count)
        self._attention_empty = muted(_NO_REPOSITORY)
        attention_body.addWidget(self._attention_empty)
        self._attention_list = QListWidget()
        self._attention_list.setMaximumHeight(160)
        self._attention_list.itemActivated.connect(lambda _item: self.ctx.navigate("failures"))
        attention_body.addWidget(self._attention_list)
        attention_actions = QHBoxLayout()
        attention_actions.addStretch(1)
        open_failures = ghost_button("Open failures")
        open_failures.clicked.connect(lambda: self.ctx.navigate("failures"))
        attention_actions.addWidget(open_failures)
        attention_body.addLayout(attention_actions)
        left.addWidget(attention_card)

        workflows_card = Card("Recent workflows")
        self._workflows_table = TablePanel(_WORKFLOW_COLUMNS, "No workflows yet", _NO_WORKFLOWS)
        workflows_card.add(self._workflows_table)
        self._workflows_table.activated.connect(
            lambda row: self.ctx.open_task(str(row.get("id") or ""), "")
        )
        left.addWidget(workflows_card, stretch=1)
        main.addLayout(left, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(DARK.space_md)
        activity_card = Card("Recent activity")
        activity_body = activity_card.body()
        # The activity card stretches to fill its column; without an
        # Expanding occupant Qt splits the spare height between the card
        # title and the empty label, inflating both. Expanding + centered
        # keeps the title at one line and mirrors the workflows card.
        self._activity_empty = muted(_NO_REPOSITORY)
        self._activity_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._activity_empty.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        activity_body.addWidget(self._activity_empty)
        self._activity_list = QListWidget()
        # Timestamped entries outgrow the narrow column; wrapped rows keep
        # the full message readable instead of a clipped horizontal scroll.
        self._activity_list.setWordWrap(True)
        activity_body.addWidget(self._activity_list)
        right.addWidget(activity_card, stretch=1)
        main.addLayout(right, stretch=2)

        root.addLayout(main, stretch=1)

        self._summary_loaded = False
        self._agents_loaded = False
        self._show_no_repo()

    def _build_header(self) -> PageHeader:
        self._page_header = PageHeader(self.title)
        # The heading keeps the view title (shell-wide header contract); the
        # Dashboard shows the repository name at 30px in its place.
        self._page_header._heading.hide()
        self._repo_title = QLabel(self.title)
        self._repo_title.setStyleSheet(f"font-size: {_REPO_TITLE_PX}px; font-weight: 600;")
        column = self._page_header.layout().itemAt(0).layout()
        column.insertWidget(0, self._repo_title)
        column.setSpacing(DARK.space_xs)
        window = Chip(f"Last {_DAYS} days", "clock")
        window.setToolTip(f"The runs chart covers the last {_DAYS} days")
        window.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._page_header.add_action(window)
        new_task = QPushButton("New task")
        new_task.setProperty("variant", "primary")
        new_task.setToolTip("Open the New Task dialog  (Ctrl+N)")
        new_task.clicked.connect(self._open_new_task)
        self._page_header.add_action(new_task)
        return self._page_header

    def _build_charts(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        runs = QVBoxLayout()
        runs.setSpacing(DARK.space_sm)
        head = QHBoxLayout()
        head.addWidget(SectionLabel("Runs over time"))
        head.addStretch(1)
        head.addWidget(_legend("Agent runs", DARK.series_1))
        head.addSpacing(DARK.space_md)
        head.addWidget(_legend("Failed or timed out", DARK.danger))
        runs.addLayout(head)
        card = _navy_card()
        self._runs_chart = AreaChart()
        self._runs_chart.setMinimumHeight(200)
        card.add(self._runs_chart)
        self._runs_empty = muted("")
        self._runs_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._runs_empty.setMinimumHeight(200)
        card.add(self._runs_empty)
        runs.addWidget(card)
        row.addLayout(runs, stretch=3)

        verification = QVBoxLayout()
        verification.setSpacing(DARK.space_sm)
        verification.addWidget(SectionLabel("Verification"))
        card = _navy_card()
        self._donut = Donut()
        self._donut.setFixedSize(150, 150)
        card.body().addWidget(self._donut, alignment=Qt.AlignmentFlag.AlignHCenter)
        legend = QGridLayout()
        legend.setVerticalSpacing(2)
        self._donut_passed = QLabel("-")
        self._donut_passed.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._verification_failed = QLabel("-")
        self._verification_failed.setStyleSheet("font-size: 20px; font-weight: 600;")
        legend.addWidget(self._donut_passed, 0, 0)
        legend.addWidget(self._verification_failed, 0, 1, Qt.AlignmentFlag.AlignRight)
        legend.addWidget(_legend("Passed", DARK.success), 1, 0)
        legend.addWidget(_legend("Failed", DARK.danger), 1, 1, Qt.AlignmentFlag.AlignRight)
        self._donut_legend = QWidget()
        self._donut_legend.setLayout(legend)
        card.add(self._donut_legend)
        self._donut_empty = muted("")
        self._donut_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._donut_empty.setWordWrap(True)
        self._donut_empty.setMinimumHeight(200)
        card.add(self._donut_empty)
        verification.addWidget(card)
        row.addLayout(verification, stretch=1)
        return row

    # ------------------------------------------------------------------
    def _stat_card(self, title: str, target: str, tooltip: str) -> tuple[StatCard, QLabel, QLabel]:
        """One clickable headline number navigating to its full view."""
        card = StatCard(title)
        card.setToolTip(tooltip)
        card.clicked.connect(lambda: self.ctx.navigate(target))
        stat = QLabel("-")
        stat.setProperty("role", "stat")
        card.body().addWidget(stat)
        detail = QLabel("")
        detail.setProperty("role", "muted")
        detail.setWordWrap(True)
        card.body().addWidget(detail)
        return card, stat, detail

    def _show_no_repo(self) -> None:
        """Empty state: no repository chosen, nothing loaded yet."""
        self.attention_changed.emit(0)
        self._summary_loaded = False
        self._agents_loaded = False
        self._repo_title.setText(self.title)
        self._page_header.set_subtitle("No repository chosen")
        self._show_charts_empty(_NO_REPOSITORY)
        for stat in (
            self._workflows_stat,
            self._tasks_stat,
            self._verification_stat,
            self._agents_stat,
        ):
            stat.setText("-")
            stat.setStyleSheet("")
        for detail in (
            self._workflows_detail,
            self._tasks_detail,
            self._verification_detail,
            self._agents_detail,
        ):
            detail.setText("")
        self._attention_count.setText("")
        self._attention_empty.setText(_NO_REPOSITORY)
        self._attention_empty.show()
        self._attention_list.hide()
        self._workflows_table.set_empty_state("No repository chosen", _NO_REPOSITORY)
        self._workflows_table.set_rows([])
        self._activity_empty.setText(_NO_REPOSITORY)
        self._activity_empty.show()
        self._activity_list.hide()

    def _show_charts_empty(self, text: str) -> None:
        self._runs_chart.hide()
        self._runs_empty.setText(text)
        self._runs_empty.show()
        self._donut.hide()
        self._donut_legend.hide()
        self._donut_empty.setText(text)
        self._donut_empty.show()
        self._window_note.hide()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._show_no_repo()
            return
        if not self._summary_loaded:
            for detail in (self._workflows_detail, self._tasks_detail, self._verification_detail):
                detail.setText("loading...")
        if not self._agents_loaded:
            self._agents_detail.setText("detecting...")
        controller = self.ctx.controller
        self.submit(
            "summary",
            lambda: controller.dashboard_summary(
                repository,
                workflow_limit=8,
                event_limit=12,
                failure_limit=10,
                verification_limit=_READ_LIMIT,
            ),
            self._on_summary,
            self._on_summary_error,
        )
        self.submit(
            "agents",
            lambda: controller.list_agent_profiles(),
            self._on_agents,
        )
        self.submit(
            "runs",
            lambda: controller.list_recent_agent_runs(repository, limit=_READ_LIMIT),
            self._on_runs,
        )

    def _open_new_task(self) -> None:
        start = self.ctx.new_task
        if start is None:
            self.ctx.toast("Unavailable", "Task creation is not wired in this context.", "error")
            return
        start()

    def on_operation_event(self, event: dict) -> None:
        if str(event.get("kind") or "") in (
            "workflow-started",
            "workflow-result",
            "result",
            "thread-finished",
        ):
            self.refresh()

    # ------------------------------------------------------------------
    def _on_summary_error(self, message: str) -> None:
        """Error state: say what failed while keeping any loaded numbers."""
        self._page_header.set_subtitle(f"State unavailable - {message}")
        if not self._summary_loaded:
            for detail in (self._workflows_detail, self._tasks_detail, self._verification_detail):
                detail.setText("unavailable")

    def _on_summary(self, summary: object) -> None:
        if not isinstance(summary, dict):
            return
        repository = str(summary.get("repository") or self.repository())
        self._repo_title.setText(PurePath(repository).name or repository)
        self._repo_title.setToolTip(repository)
        running = int((summary.get("active_workflows") or {}).get("running") or 0)
        needs = list(summary.get("needs_attention") or [])
        parts = [f"{running} running"] if running else []
        parts += [f"{len(needs)} need attention"] if needs else []
        parts.append(f"{summary.get('total_workflows', 0)} workflows")
        self._page_header.set_subtitle(" \u00b7 ".join(parts))
        self._render_verification(dict(summary.get("verification_status") or {}))

        active = summary.get("active_workflows") or {}
        pending = int(active.get("pending") or 0)
        self._workflows_stat.setText(str(running))
        self._workflows_detail.setText(f"{pending} pending")

        task_counts = dict(summary.get("task_counts") or {})
        self._tasks_stat.setText(str(int(task_counts.get("running") or 0)))
        self._tasks_detail.setText(_counts_line(task_counts, exclude="running"))

        verification = dict(summary.get("verification_status") or {})
        failed = int(verification.get("failed") or 0)
        self._verification_stat.setText(str(int(verification.get("passed") or 0)))
        self._verification_stat.setStyleSheet(f"color: {DARK.danger if failed else DARK.success};")
        self._verification_detail.setText(_counts_line(verification, exclude="passed"))

        self.attention_changed.emit(len(needs))
        self._attention_count.setText(f"{len(needs)} need attention" if needs else "")
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
        self._workflows_table.set_empty_state("No workflows yet", _NO_WORKFLOWS)
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
            item = QListWidgetItem(elide(f"{format_timestamp(timestamp)}  {message}", 110))
            item.setToolTip(f"{timestamp}\n{message}")
            self._activity_list.addItem(item)

        self._summary_loaded = True

    def _on_agents(self, profiles: object) -> None:
        if not isinstance(profiles, list):
            return
        available = sum(1 for profile in profiles if profile.get("availability"))
        self._agents_stat.setText(f"{available} / {len(profiles)}")
        self._agents_detail.setText("available now" if profiles else "none detected")
        self._agents_loaded = True

    # ------------------------------------------------------------------
    def _on_runs(self, runs: object) -> None:
        if isinstance(runs, list):
            self._runs = [run for run in runs if isinstance(run, dict)]
            self._render_runs()
            self._window_note.setText(f"Based on the newest {_READ_LIMIT} runs.")
            self._window_note.setVisible(len(runs) >= _READ_LIMIT)

    def _render_verification(self, counts: dict) -> None:
        """Donut + legend from the summary's verification outcomes."""
        counts = {str(key).lower(): int(value or 0) for key, value in counts.items()}
        passed = counts.get("passed", 0)
        failed = sum(counts.get(key, 0) for key in _DECIDED[1:])
        self._donut_passed.setText(str(passed))
        self._verification_failed.setText(str(failed))
        decided = passed + failed
        self._donut.setVisible(bool(decided))
        self._donut_legend.setVisible(bool(decided))
        self._donut_empty.setVisible(not decided)
        self._donut_empty.setText("No verification results yet.")
        if decided:
            rate = round(passed / decided * 100)
            self._donut.set_data(
                [(passed, DARK.success), (failed, DARK.danger)], f"{rate}%", "pass rate"
            )

    def _render_runs(self) -> None:
        runs = self._runs
        series = derive.daily_series(runs, _DAYS, datetime.now().astimezone())
        totals = [total for _day, total, _failed in series]
        if not any(totals):
            self._runs_chart.hide()
            self._runs_empty.setText(f"No agent runs in the last {_DAYS} days.")
            self._runs_empty.show()
            return
        labels = [_day_label(day) for day, _total, _failed in series]
        peak = max(range(len(totals)), key=lambda index: (totals[index], index))
        noun = "run" if totals[peak] == 1 else "runs"
        self._runs_chart.set_data(
            labels,
            [
                ("Agent runs", DARK.series_1, totals),
                ("Failed or timed out", DARK.danger, [f for _d, _t, f in series]),
            ],
            peak,
            f"{labels[peak]} \u00b7 {totals[peak]} {noun}",
        )
        self._runs_empty.hide()
        self._runs_chart.show()
