"""Desktop GUI: totals, daily chart, provider/model breakdown, import/export."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QSettings, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import collectors, db
from .format import human_count, human_tokens

# Theme palettes: PALETTE always holds the active theme's colors; painters
# and the stylesheet builder read from it so a toggle repaints everything.
LIGHT_THEME = {
    "accent": "#0E7C66",
    "accent_hover": "#0B6A57",
    "ink": "#1C2126",
    "muted": "#5B6470",
    "surface": "#FFFFFF",
    "canvas": "#F4F5F7",
    "border": "#E1E4E8",
    "hover": "#EEF0F2",
    "header": "#EEF0F2",
    "alt": "#FAFBFC",
    "gridline": "#EFF1F3",
    "sel_bg": "#DCEFE9",
    "sel_ink": "#1C2126",
    "disabled": "#E7E9EC",
    "amber": "#B45309",  # marks estimated (non-provider-reported) usage
}
DARK_THEME = {
    "accent": "#2AA88F",
    "accent_hover": "#1F8F76",
    "ink": "#E8EAED",
    "muted": "#98A1AC",
    "surface": "#20242A",
    "canvas": "#17191D",
    "border": "#333A42",
    "hover": "#2A3037",
    "header": "#262B31",
    "alt": "#1C2025",
    "gridline": "#2A3037",
    "sel_bg": "#143D33",
    "sel_ink": "#E8EAED",
    "disabled": "#23272C",
    "amber": "#F59E0B",
}
PALETTE = dict(LIGHT_THEME)


def build_stylesheet(dark: bool) -> str:
    c = DARK_THEME if dark else LIGHT_THEME
    return f"""
QMainWindow, QWidget#central {{ background: {c['canvas']}; }}
QLabel#appTitle {{ font-size: 16px; font-weight: 700; color: {c['ink']}; }}
QFrame#card {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 6px;
}}
QLabel#statValue {{ font-size: 26px; font-weight: 700; color: {c['ink']}; }}
QLabel#statCaption {{ font-size: 11px; color: {c['muted']}; }}
QLabel#panelTitle {{ font-size: 12px; font-weight: 600; color: {c['muted']}; }}
QLabel#chartCaption {{ font-size: 11px; color: {c['muted']}; }}
QLabel#syncLabel, QLabel#countLabel {{ font-size: 11px; color: {c['muted']}; }}
QPushButton {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    padding: 6px 14px;
    color: {c['ink']};
}}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton#primary {{
    background: {c['accent']};
    border: 1px solid {c['accent']};
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton:disabled {{
    background: {c['disabled']};
    border: 1px solid {c['border']};
    color: {c['muted']};
}}
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    background: {c['canvas']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {c['muted']};
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {c['canvas']};
    color: {c['ink']};
    border: 1px solid {c['border']};
    border-bottom: none;
    border-radius: 6px 6px 0 0;
}}
QLineEdit, QComboBox {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 5px;
    padding: 5px 8px;
    font-size: 12px;
    color: {c['ink']};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    color: {c['ink']};
    selection-background-color: {c['sel_bg']};
    selection-color: {c['sel_ink']};
}}
QComboBox QAbstractItemView::item {{
    background: {c['surface']};
    color: {c['ink']};
    padding: 4px 8px;
}}
QComboBox QAbstractItemView::item:selected {{
    background: {c['sel_bg']};
    color: {c['sel_ink']};
}}
QTableWidget {{
    background: {c['surface']};
    alternate-background-color: {c['alt']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    gridline-color: {c['gridline']};
    font-size: 12px;
}}
QHeaderView::section {{
    background: {c['header']};
    border: none;
    border-bottom: 1px solid {c['border']};
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 600;
    color: {c['muted']};
}}
QTableWidget::item {{ padding: 4px 8px; color: {c['ink']}; }}
QTableWidget::item:selected {{ background: {c['sel_bg']}; color: {c['sel_ink']}; }}
QStatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
"""


def resolve_db_path() -> Path:
    env = os.environ.get("AI_TOKEN_TRACKER_DB")
    if env:
        return Path(env)
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/ai-token-tracker/usage.db")
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ai-token-tracker" / "usage.db"


class DailyChart(QWidget):
    """Bar chart of daily token totals over the window shown in the caption."""

    def __init__(self) -> None:
        super().__init__()
        self._series: list[tuple[str, int]] = []
        self.setMinimumHeight(190)

    def set_series(self, series: list[tuple[str, int]]) -> None:
        self._series = series
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._series or not any(value for _, value in self._series):
            painter.setPen(QColor(PALETTE["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "No usage data yet. Click Sync sources.")
            return
        area = QRectF(self.rect()).adjusted(10, 10, -10, -26)
        max_value = max(value for _, value in self._series) or 1
        count = len(self._series)
        gap = 2.0
        bar_width = max(1.0, (area.width() - gap * (count - 1)) / count)
        accent = QColor(PALETTE["accent"])
        for index, (_day, value) in enumerate(self._series):
            if value <= 0:
                continue
            height = area.height() * value / max_value
            x = area.left() + index * (bar_width + gap)
            painter.fillRect(QRectF(x, area.bottom() - height, bar_width, height), accent)
        painter.setPen(QPen(QColor(PALETTE["border"])))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.setPen(QColor(PALETTE["muted"]))
        painter.drawText(
            QRectF(area.left(), self.rect().top(), area.width(), 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"peak {human_tokens(max_value)}",
        )
        painter.drawText(
            QRectF(area.left(), area.bottom() + 4, area.width() / 2, 18),
            Qt.AlignLeft,
            self._series[0][0],
        )
        painter.drawText(
            QRectF(area.left() + area.width() / 2, area.bottom() + 4, area.width() / 2, 18),
            Qt.AlignRight,
            self._series[-1][0],
        )


def _stat_card(caption: str) -> tuple[QFrame, QLabel, QLabel]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 10, 14, 10)
    value = QLabel("0")
    value.setObjectName("statValue")
    label = QLabel(caption)
    label.setObjectName("statCaption")
    layout.addWidget(value)
    layout.addWidget(label)
    return card, value, label


class NumberDelegate(QStyledItemDelegate):
    """Render a raw stored number through a human formatter.

    Numeric cells keep the plain int in DisplayRole (so Qt's default
    DisplayRole sorting stays numeric) and only format at paint time.
    """

    def __init__(self, formatter, parent=None) -> None:
        super().__init__(parent)
        self._formatter = formatter

    def displayText(self, value, _locale):
        try:
            return self._formatter(int(value))
        except (TypeError, ValueError):
            return str(value)


def _text_item(text: str, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align_right:
        item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
    return item


def _number_item(value: int, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setData(Qt.DisplayRole, int(value))
    if align_right:
        item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
    return item


class MainWindow(QMainWindow):
    def __init__(self, conn, *, dark: bool = False) -> None:
        super().__init__()
        self.conn = conn
        self._events = []
        self._dark = dark
        self._statuses: list[dict] = []
        self.setWindowTitle("AI Token Tracker")
        self.resize(1180, 820)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("AI Token Tracker")
        title.setObjectName("appTitle")
        header.addWidget(title)
        header.addStretch()
        self.sync_label = QLabel("Not synced yet")
        self.sync_label.setObjectName("syncLabel")
        header.addWidget(self.sync_label)
        header.addSpacing(8)
        self.import_button = QPushButton("Sync sources")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.sync_sources)
        self.import_file_button = QPushButton("Import CSV/JSON")
        self.import_file_button.clicked.connect(self.import_file)
        self.export_button = QPushButton("Export CSV")
        self.export_button.clicked.connect(self.export_csv)
        self.export_json_button = QPushButton("Export JSON")
        self.export_json_button.clicked.connect(self.export_json)
        self.theme_button = QPushButton("Light" if dark else "Dark")
        self.theme_button.setToolTip("Switch to the light or dark theme")
        self.theme_button.clicked.connect(self.toggle_theme)
        for button in (
            self.import_button, self.import_file_button, self.export_button,
            self.export_json_button, self.theme_button,
        ):
            header.addWidget(button)
        root.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard(), "Dashboard")
        tabs.addTab(self._build_sources_page(), "Sources")
        tabs.addTab(self._build_events_page(), "Recent events")
        root.addWidget(tabs, stretch=1)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status = status
        self.status.showMessage(f"Database: {db_path_label(conn)}", 12000)

    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(12)

        stats = QGridLayout()
        stats.setSpacing(10)
        # One card per range; each shows total plus the exact/estimated split.
        self.range_cards: dict[str, tuple[QLabel, QLabel]] = {}
        for column, (caption, key) in enumerate(
            (
                ("tokens today (UTC)", "today"),
                ("tokens, last 7 days", "week"),
                ("tokens, last 30 days", "month"),
                ("tokens, all time", "life"),
            )
        ):
            card, value_label, _caption_label = _stat_card(caption)
            detail_label = QLabel("exact 0 · estimated 0")
            detail_label.setObjectName("statCaption")
            card.layout().addWidget(detail_label)
            self.range_cards[key] = (value_label, detail_label)
            stats.addWidget(card, 0, column)
            stats.setColumnStretch(column, 1)
        layout.addLayout(stats)

        chart_caption = QLabel("Daily tokens, last 30 days (UTC)")
        chart_caption.setObjectName("chartCaption")
        layout.addWidget(chart_caption)
        self.chart = DailyChart()
        layout.addWidget(self.chart, stretch=3)

        breakdown_row = QHBoxLayout()
        breakdown_row.setSpacing(10)
        self.breakdown_panes = []
        for default_column in ("Tool", "Provider", "Model"):
            pane, combo, table = self._breakdown_pane(default_column)
            self.breakdown_panes.append((combo, table))
            breakdown_row.addWidget(pane, stretch=1)
        layout.addLayout(breakdown_row, stretch=2)
        return page

    def _build_sources_page(self) -> QWidget:
        """Agents/Providers/Models rollups plus collector coverage and status."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(12)

        self.coverage_label = QLabel("Tracked agents 0/0 · exact 0/0 · unavailable 0")
        self.coverage_label.setObjectName("countLabel")
        layout.addWidget(self.coverage_label)

        rollups = QHBoxLayout()
        rollups.setSpacing(10)
        self.sources_tables: dict[str, QTableWidget] = {}
        for column, title in (("tool", "Agents"), ("provider", "Providers"), ("model", "Models")):
            pane = QFrame()
            pane.setObjectName("card")
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(12, 10, 12, 10)
            pane_layout.setSpacing(6)
            heading = QLabel(title)
            heading.setObjectName("panelTitle")
            table = self._breakdown_table(title, "Tokens", "Requests")
            pane_layout.addWidget(heading)
            pane_layout.addWidget(table, stretch=1)
            rollups.addWidget(pane, stretch=1)
            self.sources_tables[column] = table
        layout.addLayout(rollups, stretch=2)

        status_heading = QLabel("Collector status")
        status_heading.setObjectName("panelTitle")
        layout.addWidget(status_heading)
        self.status_table = QTableWidget(0, 6)
        self.status_table.setHorizontalHeaderLabels(
            ["Status", "Source", "Tool", "Last sync", "Events", "Error"]
        )
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setSelectionBehavior(QTableWidget.SelectRows)
        status_header = self.status_table.horizontalHeader()
        status_header.setSectionResizeMode(0, QHeaderView.Fixed)
        status_header.resizeSection(0, 118)
        status_header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.status_table.setColumnWidth(1, 130)
        self.status_table.setColumnWidth(2, 140)
        self.status_table.setColumnWidth(3, 165)
        self.status_table.setColumnWidth(4, 90)
        layout.addWidget(self.status_table, stretch=1)
        return page

    def _breakdown_pane(self, default_column: str) -> tuple[QWidget, QComboBox, QTableWidget]:
        """One breakdown panel: heading, group-by selector, sortable table."""
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        heading = QLabel(f"By {default_column.lower()}")
        heading.setObjectName("panelTitle")
        combo = QComboBox()
        combo.addItems(["Tool", "Provider", "Model", "Project", "Agent"])
        combo.setCurrentText(default_column)
        table = self._breakdown_table(default_column, "Tokens", "Requests")

        def repopulate(text: str) -> None:
            heading.setText(f"By {text.lower()}")
            table.setHorizontalHeaderLabels([text, "Tokens", "Requests"])
            self._fill_table(table, db.breakdown(self.conn, text.lower()))

        combo.currentTextChanged.connect(repopulate)

        top = QHBoxLayout()
        top.addWidget(heading)
        top.addStretch()
        top.addWidget(combo)
        layout.addLayout(top)
        layout.addWidget(table, stretch=1)
        return pane, combo, table

    def _build_events_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.filter_search = QLineEdit()
        self.filter_search.setPlaceholderText("Search provider, model, agent, project")
        self.filter_search.setClearButtonEnabled(True)
        filters.addWidget(self.filter_search, stretch=1)
        self.filter_provider = QComboBox()
        self.filter_provider.addItem("All providers")
        filters.addWidget(self.filter_provider)
        self.filter_kind = QComboBox()
        self.filter_kind.addItems(["All usage", "Exact only", "Estimated only"])
        filters.addWidget(self.filter_kind)
        self.events_count = QLabel("0 events")
        self.events_count.setObjectName("countLabel")
        filters.addWidget(self.events_count)
        layout.addLayout(filters)

        self.events_table = QTableWidget(0, 7)
        self.events_table.setHorizontalHeaderLabels(
            ["Time (UTC)", "Provider", "Model", "Agent", "Project", "Tokens", "Kind"]
        )
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.events_table.setAlternatingRowColors(True)
        header = self.events_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 150)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, 110)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 90)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        # Fixed widths for the measured columns: ResizeToContents re-measures
        # the whole column on every setItem, which freezes a 5k-row populate.
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.resizeSection(5, 96)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        header.resizeSection(6, 84)
        self.events_table.setItemDelegateForColumn(5, NumberDelegate(human_tokens, self.events_table))
        header.setSortIndicator(0, Qt.DescendingOrder)
        self.events_table.setSortingEnabled(True)
        layout.addWidget(self.events_table, stretch=1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._populate_events_table)
        self.filter_search.textChanged.connect(lambda _text: self._filter_timer.start())
        self.filter_provider.currentTextChanged.connect(lambda _text: self._populate_events_table())
        self.filter_kind.currentTextChanged.connect(lambda _text: self._populate_events_table())
        return page

    def _breakdown_table(self, *headers: str) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            header.resizeSection(column, 96)
        table.setItemDelegateForColumn(1, NumberDelegate(human_tokens, table))
        table.setItemDelegateForColumn(2, NumberDelegate(human_count, table))
        header.setSortIndicator(1, Qt.DescendingOrder)
        table.setSortingEnabled(True)
        return table

    def refresh(self) -> None:
        today = datetime.now(timezone.utc).date()
        ranges = {
            "today": db.totals(self.conn, today.isoformat()),
            "week": db.totals(self.conn, (today - timedelta(days=6)).isoformat()),
            "month": db.totals(self.conn, (today - timedelta(days=29)).isoformat()),
            "life": db.totals(self.conn),
        }
        for key, (value_label, detail_label) in self.range_cards.items():
            window_totals = ranges[key]
            value_label.setText(human_tokens(window_totals["total_tokens"]))
            detail_label.setText(
                f"exact {human_tokens(window_totals['exact_tokens'])} · "
                f"estimated {human_tokens(window_totals['estimated_tokens'])}"
            )

        self.chart.set_series(db.daily_series(self.conn, days=30))
        for combo, table in self.breakdown_panes:
            self._fill_table(table, db.breakdown(self.conn, combo.currentText().lower()))
        self._fill_sources()
        self._events = list(reversed(db.all_events(self.conn)))
        self._rebuild_provider_filter()
        self._populate_events_table()

    def _fill_sources(self) -> None:
        """Rollup tables, coverage counts, and the collector status rows."""
        for column, table in self.sources_tables.items():
            self._fill_table(table, db.breakdown(self.conn, column))

        stored = {row["source"]: row for row in db.collector_statuses(self.conn)}
        statuses: list[dict] = []
        for spec in collectors.REGISTRY:
            row = stored.get(spec.source)
            if row is not None:
                statuses.append(row)
            else:
                # Never synced: the registry still knows the tool and whether
                # the source is expected to be usable.
                statuses.append(
                    {
                        "source": spec.source,
                        "tool": spec.tool,
                        "capability": spec.capability,
                        "last_sync": None,
                        "events": 0,
                        "error": spec.unavailable_reason,
                    }
                )
        self._statuses = statuses

        agent_specs = [s for s in collectors.REGISTRY if s.kind == "agent"]
        agent_sources = {s.source for s in agent_specs}
        tracked = [
            s for s in statuses
            if s["source"] in agent_sources and (s["events"] or 0) > 0
        ]
        exact = [s for s in tracked if s["capability"] == "exact"]
        unavailable = [s for s in statuses if s["capability"] == "unavailable"]
        self.coverage_label.setText(
            f"Tracked agents {len(tracked)}/{len(agent_specs)} · "
            f"exact {len(exact)}/{len(agent_specs)} · "
            f"unavailable {len(unavailable)}"
        )
        self._fill_status_rows()

    def _fill_status_rows(self) -> None:
        colors = {"exact": "#16A34A", "estimated": "#B45309", "unavailable": "#6B7280"}
        labels = {"exact": "● Exact", "estimated": "● Estimated", "unavailable": "● Unavailable"}
        table = self.status_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for status in self._statuses:
            row = table.rowCount()
            table.insertRow(row)
            capability = status.get("capability") or "unavailable"
            dot = _text_item(labels.get(capability, "● Unknown"))
            dot.setForeground(QColor(colors.get(capability, "#6B7280")))
            table.setItem(row, 0, dot)
            table.setItem(row, 1, _text_item(status["source"]))
            table.setItem(row, 2, _text_item(status.get("tool") or ""))
            last_sync = status.get("last_sync")
            table.setItem(row, 3, _text_item(last_sync[:19].replace("T", " ") if last_sync else "never"))
            table.setItem(row, 4, _number_item(int(status.get("events") or 0), align_right=True))
            table.setItem(row, 5, _text_item(status.get("error") or ""))
        table.setSortingEnabled(True)

    def _fill_table(self, table: QTableWidget, rows: list[tuple[str, int, int]]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for key, total, events in rows:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, _text_item(key))
            table.setItem(row, 1, _number_item(total, align_right=True))
            table.setItem(row, 2, _number_item(events, align_right=True))
        table.setSortingEnabled(True)

    def _rebuild_provider_filter(self) -> None:
        """Keep the provider filter in sync with stored data, preserving selection."""
        current = self.filter_provider.currentText()
        providers = sorted({event.provider for event in self._events})
        self.filter_provider.blockSignals(True)
        self.filter_provider.clear()
        self.filter_provider.addItem("All providers")
        self.filter_provider.addItems(providers)
        index = self.filter_provider.findText(current)
        self.filter_provider.setCurrentIndex(index if index >= 0 else 0)
        self.filter_provider.blockSignals(False)

    def _matching_events(self) -> list:
        needle = self.filter_search.text().strip().lower()
        provider = self.filter_provider.currentText()
        kind = self.filter_kind.currentText()
        matches = []
        for event in self._events:
            if provider != "All providers" and event.provider != provider:
                continue
            if kind == "Exact only" and not event.exact:
                continue
            if kind == "Estimated only" and event.exact:
                continue
            if needle:
                haystack = " ".join(
                    part for part in (event.provider, event.model, event.agent, event.project) if part
                ).lower()
                if needle not in haystack:
                    continue
            matches.append(event)
        return matches

    def _populate_events_table(self) -> None:
        matches = self._matching_events()
        table = self.events_table
        table.setSortingEnabled(False)
        table.setRowCount(len(matches))
        for row, event in enumerate(matches):
            table.setItem(row, 0, _text_item(event.timestamp[:19].replace("T", " ")))
            table.setItem(row, 1, _text_item(event.provider))
            table.setItem(row, 2, _text_item(event.model or ""))
            table.setItem(row, 3, _text_item(event.agent or ""))
            table.setItem(row, 4, _text_item(event.project or ""))
            table.setItem(row, 5, _number_item(event.total_tokens, align_right=True))
            kind = _text_item("exact" if event.exact else "estimated")
            if not event.exact:
                kind.setForeground(QColor(PALETTE["amber"]))
            table.setItem(row, 6, kind)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.DescendingOrder)
        self.events_count.setText(f"{len(matches):,} of {len(self._events):,} events")

    def toggle_theme(self) -> None:
        self._dark = not self._dark
        PALETTE.clear()
        PALETTE.update(DARK_THEME if self._dark else LIGHT_THEME)
        QApplication.instance().setStyleSheet(build_stylesheet(self._dark))
        self.theme_button.setText("Light" if self._dark else "Dark")
        QSettings("ai-token-tracker", "token_tracker").setValue(
            "theme", "dark" if self._dark else "light"
        )
        self._fill_status_rows()  # dot colors are baked in at fill time
        self.chart.update()
        self.update()

    def _set_sync(self, text: str, error: bool = False) -> None:
        self.sync_label.setText(text)
        self.sync_label.setStyleSheet(
            f"color: {PALETTE['amber']}; font-size: 11px;" if error else ""
        )

    def sync_sources(self) -> None:
        """Run every collector (read-only) and refresh the dashboard."""
        self.import_button.setEnabled(False)
        self._set_sync("Syncing sources...")
        QApplication.processEvents()  # show the busy state before the blocking read
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = collectors.sync_all(self.conn)
        except Exception as exc:  # a locked/moved source must not kill the app
            self._set_sync(f"Sync failed: {exc}", error=True)
            self.status.showMessage(f"Sync failed: {exc}", 10000)
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.import_button.setEnabled(True)
        ok = [name for name, result in results.items() if result["status"] == "ok"]
        failed = [
            (name, result.get("error", ""))
            for name, result in results.items()
            if result["status"] == "error"
        ]
        new_total = sum(result.get("inserted", 0) for result in results.values())
        summary = (
            f"Synced {datetime.now().strftime('%H:%M:%S')} · "
            f"{human_count(new_total)} new across {len(ok)} sources"
        )
        if failed:
            summary += f" · {len(failed)} failed"
        self._set_sync(summary, error=bool(failed))
        detail = summary + (f" · failed: {', '.join(name for name, _ in failed)}" if failed else "")
        self.status.showMessage(detail, 12000)
        self.refresh()

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import events file", "", "Data files (*.csv *.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                inserted, updated, unchanged = db.import_json(self.conn, path)
            else:
                inserted, updated, unchanged = db.import_csv(self.conn, path)
        except Exception as exc:  # a malformed file must surface, not crash
            self.status.showMessage(f"Import failed: {Path(path).name}: {exc}", 10000)
            return
        self.status.showMessage(
            f"Imported {Path(path).name}: {human_count(inserted)} new, "
            f"{human_count(updated)} refreshed, {human_count(unchanged)} unchanged",
            10000,
        )
        self.refresh()

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export events as CSV", "token-usage.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            count = db.export_csv(self.conn, path)
        except (OSError, ValueError) as exc:
            self.status.showMessage(f"Export failed: {exc}", 10000)
            return
        self.status.showMessage(f"Exported {human_count(count)} events to {path}", 10000)

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export events as JSON", "token-usage.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            count = db.export_json(self.conn, path)
        except (OSError, ValueError) as exc:
            self.status.showMessage(f"Export failed: {exc}", 10000)
            return
        self.status.showMessage(f"Exported {human_count(count)} events to {path}", 10000)


def db_path_label(conn) -> str:
    return str(Path(conn.execute("PRAGMA database_list").fetchone()[2]))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Token Tracker")
    settings = QSettings("ai-token-tracker", "token_tracker")
    dark = settings.value("theme", "light") == "dark"
    if dark:
        PALETTE.clear()
        PALETTE.update(DARK_THEME)
    app.setStyleSheet(build_stylesheet(dark))
    db_path = resolve_db_path()
    conn = db.connect(db_path)
    window = MainWindow(conn, dark=dark)
    window.show()
    window.sync_sources()
    window.refresh()
    exit_code = app.exec()
    conn.close()
    return exit_code
