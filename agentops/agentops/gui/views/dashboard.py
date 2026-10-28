"""Dashboard: the current repository at a glance.

Every number derives from existing reads: ``dashboard_summary`` plus
``list_workflows``, ``list_recent_agent_runs`` and
``list_recent_verification_runs`` (each capped at the newest 500), shaped by
the Qt-free ``gui/derive.py``.
Values keep their last good state across refreshes; an empty repository
shows empty states, never zero-filled charts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import derive
from ..charts import AreaChart, Donut
from ..components import Chip, SectionLabel
from ..icons import icon
from ..tokens import DARK, with_alpha
from ..widgets import Card, PageHeader, StatCard, ghost_button, muted
from .base import BaseView
from .dashboard_parts import ActivityList, AgentBars, AttentionHeader, AttentionTable

_NO_REPOSITORY = "Pick a repository in Workflows, or with Ctrl+K, to begin."
_READ_LIMIT = 500  # newest agent / verification runs read for charts and stats
_DAYS = 14
_REPO_TITLE_PX = 30
_DECIDED = ("passed", "failed", "timed_out", "unverified")


def _duration(seconds: int) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _day_label(day) -> str:  # type: ignore[no-untyped-def]
    return f"{day:%b} {day.day}"


class _WeekCard(StatCard):
    """Navy stat card: coloured round icon, title, 28px value, delta row."""

    def __init__(self, title: str, icon_name: str, color: str, tooltip: str):
        super().__init__("")
        self.setProperty("navy", True)
        self.setToolTip(tooltip)
        row = QHBoxLayout()
        row.setSpacing(DARK.space_md)
        badge = QLabel()
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icon(icon_name, color, 20))
        badge.setStyleSheet(f"background: {with_alpha(color, 0.16)}; border-radius: 20px;")
        row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(2)
        self.title = QLabel(title)
        self.title.setProperty("role", "subtitle")
        self.title.setWordWrap(True)
        column.addWidget(self.title)
        self.value = QLabel("-")
        self.value.setProperty("role", "stat")
        column.addWidget(self.value)
        delta = QHBoxLayout()
        delta.setSpacing(DARK.space_xs)
        self.trend = QLabel()
        self.trend.setFixedSize(14, 14)
        delta.addWidget(self.trend)
        self.delta = QLabel("")
        delta.addWidget(self.delta)
        self.note = QLabel("")
        self.note.setProperty("role", "faint")
        delta.addWidget(self.note)
        delta.addStretch(1)
        column.addLayout(delta)
        row.addLayout(column, stretch=1)
        self.body().addLayout(row)

    def set_delta(self, delta: int | None, unit: str, up_is_good: bool = True) -> None:
        """``+12%`` / ``-29 pts`` with a trending icon; None -> "no prior week"."""
        if delta is None:
            self.set_note("no prior week")
            return
        good = (delta >= 0) == up_is_good
        color = DARK.success if good else DARK.danger
        sign = "+" if delta > 0 else "\u2212" if delta < 0 else "\u00b1"
        self.trend.setPixmap(icon("trending-up" if delta >= 0 else "trending-down", color, 14))
        self.trend.show()
        self.delta.setText(f"{sign}{abs(delta)}{unit}")
        self.delta.show()
        self.delta.setStyleSheet(f"color: {color};")
        self.note.setText("vs last week")

    def set_note(self, text: str) -> None:
        self.trend.hide()
        self.delta.hide()
        self.note.setText(text)


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
        # The main column scrolls; the activity feed is a 330px right panel
        # (below 1200px it moves to the end of the main column instead).
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._outer = outer
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        self._root = root
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(DARK.space_md)

        root.addWidget(self._build_header())

        root.addWidget(SectionLabel("This week"))
        root.addLayout(self._build_week())
        root.addLayout(self._build_charts())
        self._window_note = QLabel("")
        self._window_note.setProperty("role", "faint")
        self._window_note.hide()
        root.addWidget(self._window_note)

        root.addWidget(self._build_attention())
        root.addStretch(1)
        self._build_feed()
        self._feed_wide: bool | None = None
        self._place_feed(True)

        self._summary: dict = {}
        self._reads: dict[str, list] = {}
        self._summary_loaded = False
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

    def _build_week(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(14)
        self._workflows_card = _WeekCard(
            "Workflows run", "workflow", DARK.info, "Open the workflow control center"
        )
        self._verification_card = _WeekCard(
            "Verification pass rate", "shield-check", DARK.success, "Open verification runs"
        )
        self._agents_card = _WeekCard("Agent time", "timer", DARK.warning, "Open the agent roster")
        self._tasks_card = _WeekCard(
            "Needs attention", "triangle-alert", DARK.danger, "Open the task list"
        )
        self._week_grid = grid
        self._week_columns = 0
        for card, target in (
            (self._workflows_card, "workflows"),
            (self._verification_card, "verification"),
            (self._agents_card, "agents"),
            (self._tasks_card, "tasks"),
        ):
            card.clicked.connect(lambda target=target: self.ctx.navigate(target))
        self._place_week(4)
        return grid

    def _place_week(self, columns: int) -> None:
        """Four stat cards in a row, or 2x2 when the page is narrow."""
        if columns == self._week_columns:
            return
        self._week_columns = columns
        cards = (self._workflows_card, self._verification_card, self._agents_card, self._tasks_card)
        for index, card in enumerate(cards):
            self._week_grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self._week_grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        wide = self.width() >= 1200
        self._place_feed(wide)
        main = self.width() - (330 if wide else 0)
        self._place_week(4 if main >= 1040 else 2)

    def _place_feed(self, wide: bool) -> None:
        """Feed as a scrolling right panel when wide, else a card at the end."""
        if wide == self._feed_wide:
            return
        self._feed_wide = wide
        if wide:
            self._feed_scroll.setWidget(self._feed_body)
        else:
            self._feed_card.layout().addWidget(self._feed_scroll.takeWidget())
        self._feed_body.show()
        self._feed.setVisible(wide)
        self._feed_card.setVisible(not wide)

    def _build_attention(self) -> QWidget:
        section = QWidget()
        column = QVBoxLayout(section)
        column.setContentsMargins(0, DARK.space_sm, 0, 0)
        column.setSpacing(DARK.space_sm)
        head = QHBoxLayout()
        head.addWidget(SectionLabel("Needs attention"))
        head.addStretch(1)
        open_failures = ghost_button("Open Failures \u2192")
        open_failures.setStyleSheet(f"color: {DARK.accent};")
        open_failures.clicked.connect(lambda: self.ctx.navigate("failures"))
        head.addWidget(open_failures)
        column.addLayout(head)
        card = _navy_card()
        card.body().setContentsMargins(0, 0, 0, DARK.space_sm)
        card.body().setSpacing(0)
        self._attention_header = AttentionHeader()
        card.add(self._attention_header)
        self._attention_list = AttentionTable()
        self._attention_list.itemActivated.connect(lambda _item: self.ctx.navigate("failures"))
        card.add(self._attention_list)
        self._attention_empty = muted(_NO_REPOSITORY)
        self._attention_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._attention_empty.setMinimumHeight(72)
        card.add(self._attention_empty)
        column.addWidget(card)
        return section

    def _build_feed(self) -> None:
        """Activity + agent bars; ``_place_feed`` decides where they sit."""
        self._feed = QFrame()
        self._feed.setObjectName("DashFeed")
        self._feed.setFixedWidth(330)
        self._feed.setStyleSheet(
            f"QFrame#DashFeed {{ background: {DARK.sidebar}; border: none;"
            f" border-left: 1px solid {DARK.border}; }}"
        )
        frame = QVBoxLayout(self._feed)
        frame.setContentsMargins(0, 0, 0, 0)
        self._feed_scroll = QScrollArea()
        self._feed_scroll.setWidgetResizable(True)
        self._feed_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._feed_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        frame.addWidget(self._feed_scroll)
        self._outer.addWidget(self._feed)
        self._feed_card = Card()
        self._feed_card.layout().setContentsMargins(0, 0, 0, 0)
        self._root.insertWidget(self._root.count() - 1, self._feed_card)
        self._feed_body = QWidget()
        column = QVBoxLayout(self._feed_body)
        column.setContentsMargins(22, 28, 22, 22)
        column.setSpacing(DARK.space_md)
        column.addWidget(SectionLabel("Activity"))
        self._activity_empty = muted(_NO_REPOSITORY)
        self._activity_empty.setWordWrap(True)
        column.addWidget(self._activity_empty)
        self._activity_list = ActivityList()
        column.addWidget(self._activity_list)
        column.addSpacing(DARK.space_md)
        column.addWidget(SectionLabel(f"Agents \u00b7 {_DAYS} days"))
        self._agents_empty = muted("")
        self._agents_empty.setWordWrap(True)
        column.addWidget(self._agents_empty)
        self._agent_bars = AgentBars()
        column.addWidget(self._agent_bars)
        column.addStretch(1)

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
        self._verification_stat = QLabel("-")
        self._verification_stat.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._verification_failed = QLabel("-")
        self._verification_failed.setStyleSheet("font-size: 20px; font-weight: 600;")
        legend.addWidget(self._verification_stat, 0, 0)
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

    def _show_no_repo(self) -> None:
        """Empty state: no repository chosen, nothing loaded yet."""
        self.attention_changed.emit(0)
        self._summary_loaded = False
        self._summary = {}
        self._reads = {}
        self._repo_title.setText(self.title)
        self._page_header.set_subtitle("No repository chosen")
        self._show_charts_empty(_NO_REPOSITORY)
        for card in (
            self._workflows_card,
            self._verification_card,
            self._agents_card,
            self._tasks_card,
        ):
            card.value.setText("-")
            card.value.setStyleSheet("")
            card.set_note("")
        self._show_lists_empty(_NO_REPOSITORY)

    def _show_lists_empty(self, text: str) -> None:
        self._attention_header.hide()
        self._attention_list.set_rows([])
        self._attention_list.hide()
        self._attention_empty.setText(text)
        self._attention_empty.show()
        self._activity_list.set_rows([])
        self._activity_list.hide()
        self._activity_empty.setText(text)
        self._activity_empty.show()
        self._agent_bars.set_bars([])
        self._agents_empty.setText(text)
        self._agents_empty.show()

    def _show_charts_empty(self, text: str) -> None:
        self._runs_chart.hide()
        self._runs_empty.setText(text)
        self._runs_empty.show()
        self._donut.hide()
        self._donut_legend.hide()
        self._donut_empty.setText(text)
        self._donut_empty.show()
        self._verification_stat.setText("-")
        self._verification_stat.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._window_note.hide()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._show_no_repo()
            return
        controller = self.ctx.controller
        self.submit(
            "summary",
            lambda: controller.dashboard_summary(
                repository,
                workflow_limit=8,
                event_limit=8,
                failure_limit=10,
                verification_limit=_READ_LIMIT,
            ),
            self._on_summary,
            self._on_summary_error,
        )
        for key, read in (
            ("workflows", lambda: controller.list_workflows(repository, limit=100)),
            ("runs", lambda: controller.list_recent_agent_runs(repository, limit=_READ_LIMIT)),
            (
                "verifications",
                lambda: controller.list_recent_verification_runs(repository, limit=_READ_LIMIT),
            ),
        ):
            self.submit(key, read, lambda result, key=key: self._on_read(key, result))

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

    def _on_read(self, key: str, result: object) -> None:
        if key == "workflows":
            result = result.get("workflows") if isinstance(result, dict) else None
        if not isinstance(result, list):
            return
        self._reads[key] = [row for row in result if isinstance(row, dict)]
        self._render_week()
        self._render_lists()
        if key == "runs":
            self._render_runs()

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
        self._summary = summary
        self._render_verification(dict(summary.get("verification_status") or {}))
        self._render_week()

        self.attention_changed.emit(len(needs))
        self._render_lists()
        self._summary_loaded = True

    def _render_verification(self, counts: dict) -> None:
        """Donut + legend from the summary's verification outcomes."""
        counts = {str(key).lower(): int(value or 0) for key, value in counts.items()}
        passed = counts.get("passed", 0)
        failed = sum(counts.get(key, 0) for key in _DECIDED[1:])
        self._verification_stat.setText(str(passed))
        color = DARK.danger if failed else DARK.success
        self._verification_stat.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {color};")
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
        runs = self._reads.get("runs", [])
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

    def _render_week(self) -> None:
        reads = self._reads
        if not self._summary or not all(k in reads for k in ("workflows", "runs", "verifications")):
            return
        needs = len(self._summary.get("needs_attention") or [])
        stats = derive.week_stats(
            reads["workflows"],
            reads["runs"],
            reads["verifications"],
            needs,
            datetime.now().astimezone(),
        )
        workflows, rate = stats["workflows"], stats["pass_rate"]
        self._workflows_card.value.setText(str(workflows["value"]))
        self._workflows_card.set_delta(workflows["delta"], "%")
        if rate["value"] is None:
            self._verification_card.value.setText("-")
            self._verification_card.value.setStyleSheet("")
            self._verification_card.set_note("no results this week")
        else:
            self._verification_card.value.setText(f"{rate['value']}%")
            color = DARK.success if rate["value"] == 100 else DARK.danger
            self._verification_card.value.setStyleSheet(f"color: {color};")
            self._verification_card.set_delta(rate["delta"], " pts")
        seconds = stats["agent_time"]
        has_runs = any(derive.parse_ts(r.get("started_at")) for r in reads["runs"])
        self._agents_card.value.setText(_duration(seconds["value"]) if has_runs else "-")
        self._agents_card.set_delta(seconds["delta"], "%", up_is_good=False)
        self._tasks_card.value.setText(str(needs))
        self._tasks_card.set_note("retryable or repairable")
        capped = len(reads["runs"]) >= _READ_LIMIT or len(reads["verifications"]) >= _READ_LIMIT
        self._window_note.setText(f"Based on the newest {_READ_LIMIT} runs.")
        self._window_note.setVisible(capped)

    def _render_lists(self) -> None:
        """Attention table, activity feed and agent bars (joins on the reads)."""
        if not self._summary:
            return
        now = datetime.now().astimezone()
        workflows = self._reads.get("workflows", [])
        runs = self._reads.get("runs", [])
        needs = list(self._summary.get("needs_attention") or [])
        rows = derive.attention_rows(needs, workflows, runs, now)
        self._attention_list.set_rows(rows)
        self._attention_header.setVisible(bool(rows))
        self._attention_list.setVisible(bool(rows))
        self._attention_empty.setVisible(not rows)
        self._attention_empty.setText("Nothing needs attention right now.")
        titles = {str(w.get("id") or ""): str(w.get("description") or "") for w in workflows}
        feed = []
        for event in self._summary.get("recent_events") or []:
            message = str(event.get("message") or event.get("type") or "")
            title, _sep, rest = message.partition(": ")
            name, color = derive.event_icon(event.get("type"), event.get("severity"))
            stamp = str(event.get("timestamp") or "")
            feed.append(
                {
                    "title": title,
                    "detail": rest or titles.get(str(event.get("workflow_id") or ""), ""),
                    "age": derive.rel_age(stamp, now),
                    "icon": name,
                    "color": color,
                    "tooltip": f"{stamp}\n{message}" if stamp else message,
                }
            )
        self._activity_list.set_rows(feed)
        self._activity_list.setVisible(bool(feed))
        self._activity_empty.setVisible(not feed)
        self._activity_empty.setText("No activity recorded yet.")
        bars = derive.agent_bars(runs, now, _DAYS)
        self._agent_bars.set_bars(bars)
        self._agents_empty.setVisible(not bars)
        self._agents_empty.setText(f"No agent runs in the last {_DAYS} days.")
