"""Desktop GUI: totals, daily chart, provider/model breakdown, import/export."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import db, opencode
from .format import human_count, human_tokens

ACCENT = "#0E7C66"
INK = "#1C2126"
MUTED = "#5B6470"
SURFACE = "#FFFFFF"
CANVAS = "#F4F5F7"
BORDER = "#E1E4E8"

STYLESHEET = f"""
QMainWindow, QWidget#central {{ background: {CANVAS}; }}
QLabel#appTitle {{ font-size: 16px; font-weight: 700; color: {INK}; }}
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QLabel#statValue {{ font-size: 26px; font-weight: 700; color: {INK}; }}
QLabel#statCaption {{ font-size: 11px; color: {MUTED}; }}
QLabel#panelTitle {{ font-size: 12px; font-weight: 600; color: {MUTED}; }}
QLabel#chartCaption {{ font-size: 11px; color: {MUTED}; }}
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 14px;
    color: {INK};
}}
QPushButton:hover {{ background: #EEF0F2; }}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #0B6A57; }}
QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: #EFF1F3;
    font-size: 12px;
}}
QHeaderView::section {{
    background: #EEF0F2;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 600;
    color: {MUTED};
}}
QTableWidget::item {{ padding: 4px 8px; color: {INK}; }}
QTableWidget::item:selected {{ background: #DCEFE9; color: {INK}; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
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
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "No usage data yet. Click Import OpenCode.")
            return
        area = QRectF(self.rect()).adjusted(10, 10, -10, -26)
        max_value = max(value for _, value in self._series) or 1
        count = len(self._series)
        gap = 2.0
        bar_width = max(1.0, (area.width() - gap * (count - 1)) / count)
        accent = QColor(ACCENT)
        for index, (_day, value) in enumerate(self._series):
            if value <= 0:
                continue
            height = area.height() * value / max_value
            x = area.left() + index * (bar_width + gap)
            painter.fillRect(QRectF(x, area.bottom() - height, bar_width, height), accent)
        painter.setPen(QPen(QColor(BORDER)))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.setPen(QColor(MUTED))
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


class MainWindow(QMainWindow):
    def __init__(self, conn) -> None:
        super().__init__()
        self.conn = conn
        self.setWindowTitle("AI Token Tracker")
        self.resize(1080, 740)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("AI Token Tracker")
        title.setObjectName("appTitle")
        header.addWidget(title)
        header.addStretch()
        self.import_button = QPushButton("Import OpenCode")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(self.import_opencode)
        self.export_button = QPushButton("Export CSV")
        self.export_button.clicked.connect(self.export_csv)
        header.addWidget(self.import_button)
        header.addWidget(self.export_button)
        root.addLayout(header)

        stats = QGridLayout()
        stats.setSpacing(10)
        self.today_card, self.today_value, _ = _stat_card("tokens today (UTC)")
        self.week_card, self.week_value, _ = _stat_card("tokens, last 7 days")
        self.life_card, self.life_value, self.life_caption = _stat_card("tokens, all time")
        stats.addWidget(self.today_card, 0, 0)
        stats.addWidget(self.week_card, 0, 1)
        stats.addWidget(self.life_card, 0, 2)
        stats.setColumnStretch(0, 1)
        stats.setColumnStretch(1, 1)
        stats.setColumnStretch(2, 1)
        root.addLayout(stats)

        chart_caption = QLabel("Daily tokens, last 30 days (UTC)")
        chart_caption.setObjectName("chartCaption")
        root.addWidget(chart_caption)
        self.chart = DailyChart()
        root.addWidget(self.chart, stretch=3)

        breakdown_row = QHBoxLayout()
        breakdown_row.setSpacing(10)
        self.provider_table = self._breakdown_table("Provider", "Tokens", "Requests")
        self.model_table = self._breakdown_table("Model", "Tokens", "Requests")
        breakdown_row.addWidget(self.provider_table, stretch=1)
        breakdown_row.addWidget(self.model_table, stretch=1)
        root.addLayout(breakdown_row, stretch=2)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status = status
        self.status.showMessage(f"Database: {db_path_label(conn)}", 12000)

    def _breakdown_table(self, *headers: str) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        return table

    def refresh(self) -> None:
        today = datetime.now(timezone.utc).date()
        week_start = (today - timedelta(days=6)).isoformat()
        today_totals = db.totals(self.conn, today.isoformat())
        week_totals = db.totals(self.conn, week_start)
        life_totals = db.totals(self.conn)

        self.today_value.setText(human_tokens(today_totals["total_tokens"]))
        self.week_value.setText(human_tokens(week_totals["total_tokens"]))
        self.life_value.setText(human_tokens(life_totals["total_tokens"]))
        self.life_caption.setText(
            f"{human_count(life_totals['events'])} requests | "
            f"{human_tokens(life_totals['exact_tokens'])} exact, "
            f"{human_tokens(life_totals['estimated_tokens'])} estimated"
        )

        self.chart.set_series(db.daily_series(self.conn, days=30))
        self._fill_table(self.provider_table, db.breakdown(self.conn, "provider"))
        self._fill_table(self.model_table, db.breakdown(self.conn, "model"))

    def _fill_table(self, table: QTableWidget, rows: list[tuple[str, int, int]]) -> None:
        table.setRowCount(0)
        for key, total, events in rows:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(human_tokens(total)))
            table.setItem(row, 2, QTableWidgetItem(human_count(events)))

    def import_opencode(self) -> None:
        source = opencode.find_database()
        if source is None:
            self.status.showMessage("OpenCode database not found. Nothing imported.", 8000)
            return
        try:
            events = opencode.collect(source)
            inserted, updated, unchanged = db.insert_events(self.conn, events)
        except Exception as exc:  # a locked/moved source DB must not kill the app
            self.status.showMessage(f"OpenCode import failed: {exc}", 10000)
            return
        self.status.showMessage(
            f"OpenCode import: {human_count(inserted)} new, {human_count(updated)} refreshed, "
            f"{human_count(unchanged)} unchanged",
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
        except OSError as exc:
            self.status.showMessage(f"Export failed: {exc}", 10000)
            return
        self.status.showMessage(f"Exported {human_count(count)} events to {path}", 10000)


def db_path_label(conn) -> str:
    return str(Path(conn.execute("PRAGMA database_list").fetchone()[2]))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Token Tracker")
    app.setStyleSheet(STYLESHEET)
    db_path = resolve_db_path()
    conn = db.connect(db_path)
    window = MainWindow(conn)
    window.show()
    window.import_opencode()
    window.refresh()
    exit_code = app.exec()
    conn.close()
    return exit_code
