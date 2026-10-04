"""Shared widgets for the AgentOps desktop client.

All views compose these primitives (cards, badges, tables, detail grids,
pipeline stages) so list/detail surfaces stay visually and behaviorally
consistent. Colors and metrics come from ``tokens`` - no literals here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from PySide6.QtCore import (
    QAbstractTableModel,
    QEasingCurve,
    QModelIndex,
    QPropertyAnimation,
    QSortFilterProxyModel,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .tokens import DARK, Theme, status_colors, with_alpha

# Qt's QWIDGETSIZE_MAX (largest permitted widget maximumHeight); PySide6 does
# not re-export it from QtWidgets on all versions.
QWIDGETSIZE_MAX = 0xFFFFFF


# ---------------------------------------------------------------------------
# labels and helpers
# ---------------------------------------------------------------------------

def label(text: str = "", role: str = "", parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    if role:
        widget.setProperty("role", role)
    return widget


def muted(text: str = "", parent: QWidget | None = None) -> QLabel:
    return label(text, "muted", parent)


def faint(text: str = "", parent: QWidget | None = None) -> QLabel:
    return label(text, "faint", parent)


def mono(text: str = "", parent: QWidget | None = None) -> QLabel:
    widget = label(text, "mono", parent)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def divider(parent: QWidget | None = None) -> QFrame:
    frame = QFrame(parent)
    frame.setProperty("role", "divider")
    frame.setFrameShape(QFrame.Shape.NoFrame)
    frame.setMaximumHeight(1)
    return frame


def ghost_button(text: str, parent: QWidget | None = None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setProperty("variant", "ghost")
    return button


def danger_button(text: str, parent: QWidget | None = None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setProperty("variant", "danger")
    return button


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------

class Card(QFrame):
    """Bordered surface grouping one dashboard/detail block."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(DARK.space_lg, DARK.space_md,
                                         DARK.space_lg, DARK.space_md)
        self.layout().setSpacing(DARK.space_md)
        self._title = QLabel(title) if title else None
        if self._title is not None:
            self._title.setProperty("role", "section")
            self.layout().addWidget(self._title)

    def body(self) -> QVBoxLayout:
        return self.layout()  # type: ignore[return-value]

    def add(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)


class SectionHeader(QWidget):
    """Title row with optional subtitle and trailing actions."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(DARK.space_sm)
        column = QVBoxLayout()
        column.setSpacing(0)
        self._heading = QLabel(title)
        self._heading.setProperty("role", "section")
        column.addWidget(self._heading)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setProperty("role", "subtitle")
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        layout.addLayout(column)
        layout.addStretch(1)

    def set_title(self, text: str) -> None:
        self._heading.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)  # type: ignore[arg-type]


class PageHeader(QWidget):
    """View-level header: page title, optional subtitle, trailing actions.

    Every shell view starts with one so the page title sits visibly above
    the section titles inside cards and panes.
    """

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(DARK.space_sm)
        column = QVBoxLayout()
        column.setSpacing(0)
        self._heading = QLabel(title)
        self._heading.setProperty("role", "page")
        column.addWidget(self._heading)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setProperty("role", "subtitle")
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        layout.addLayout(column)
        layout.addStretch(1)

    def set_title(self, text: str) -> None:
        self._heading.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)  # type: ignore[arg-type]


class EmptyState(QWidget):
    """Centered placeholder shown when a surface has nothing to display."""

    def __init__(self, heading: str, message: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DARK.space_xl, DARK.space_xl, DARK.space_xl, DARK.space_xl)
        layout.setSpacing(DARK.space_sm)
        layout.addStretch(1)
        self._heading = QLabel(heading)
        self._heading.setProperty("role", "title")
        self._heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._heading)
        self._message = QLabel(message)
        self._message.setProperty("role", "muted")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        self._action_row = QHBoxLayout()
        self._action_row.addStretch(1)
        layout.addLayout(self._action_row)
        layout.addStretch(1)

    def set_action(self, text: str, on_click: Callable[[], None]) -> None:
        button = QPushButton(text)
        button.setProperty("variant", "primary")
        button.clicked.connect(on_click)
        self._action_row.addWidget(button)

    def set_state(self, heading: str, message: str = "") -> None:
        self._heading.setText(heading)
        self._message.setText(message)


class CollapsibleSection(QFrame):
    """Card with a header toggle and a quick height animation."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(DARK.space_md, 0, DARK.space_md, DARK.space_md)
        outer.setSpacing(0)

        self._toggle = QPushButton(title)
        self._toggle.setCheckable(True)
        self._toggle.setProperty("variant", "ghost")
        self._toggle.setStyleSheet("text-align: left; font-weight: 600;")
        self._toggle.clicked.connect(self._on_toggled)
        outer.addWidget(self._toggle)

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(DARK.space_xs, 0,
                                                   DARK.space_xs, 0)
        self._container_layout.setSpacing(DARK.space_md)
        outer.addWidget(self._container)

        self._animation = QPropertyAnimation(self._container, b"maximumHeight", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add(self, widget: QWidget) -> None:
        self._container_layout.addWidget(widget)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        self._toggle.setChecked(expanded)
        self._on_toggled(animate=animate)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def _on_toggled(self, _checked: bool = False, animate: bool = True) -> None:
        expanded = self._toggle.isChecked()
        self._toggle.setText(("▾ " if expanded else "▸ ") + self._toggle.text().lstrip("▾▸ "))
        self._animation.stop()
        if not animate:
            self._container.setMaximumHeight(QWIDGETSIZE_MAX if expanded else 0)
            return
        target = self._container.sizeHint().height() if expanded else 0
        self._animation.setStartValue(self._container.maximumHeight())
        self._animation.setEndValue(target)
        try:
            self._animation.finished.disconnect(self._unclamp)
        except (RuntimeError, TypeError):
            pass
        if expanded:
            self._animation.finished.connect(self._unclamp, Qt.ConnectionType.SingleShotConnection)
        self._animation.start()

    def _unclamp(self) -> None:
        # let an expanded section grow with its content after the intro animation
        if self._toggle.isChecked():
            self._container.setMaximumHeight(QWIDGETSIZE_MAX)


class PulsingDot(QWidget):
    """Small status circle; pulses while a stage/operation is active."""

    def __init__(self, color: str = DARK.accent, size: int = 10,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self._size = size
        self._phase = 1.0
        self.setFixedSize(size, size)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(900)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_phase)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def set_pulsing(self, pulsing: bool) -> None:
        if pulsing:
            if self._animation.state() != QVariantAnimation.State.Running:
                self._animation.start()
        else:
            self._animation.stop()
            self._phase = 1.0
            self.update()

    def _on_phase(self, value: object) -> None:
        self._phase = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) / 2 - 1
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(with_alpha(self._color, 0.22)))
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(QColor(with_alpha(self._color, 0.35 + 0.65 * self._phase)))
        painter.drawEllipse(center, radius * 0.6, radius * 0.6)
        painter.end()


# ---------------------------------------------------------------------------
# status badge
# ---------------------------------------------------------------------------

class Badge(QLabel):
    """Colored status pill driven by ``tokens.status_colors``."""

    def __init__(self, text: str = "", status: object | None = None,
                 parent: QWidget | None = None, theme: Theme = DARK):
        super().__init__(parent)
        self._theme = theme
        self.setWordWrap(False)
        self.setStyleSheet(
            f"QLabel {{ padding: 2px 8px; border-radius: 9px; "
            f"font-size: {theme.size_small}px; font-weight: 600; }}"
        )
        if status is not None:
            self.set_status(status)
        else:
            self.setText(text)

    def set_status(self, status: object) -> None:
        foreground, background = status_colors(status, self._theme)
        text = str(getattr(status, "value", status) or "")
        self.setText(text.upper())
        self.setStyleSheet(
            f"QLabel {{ color: {foreground}; padding: 2px 8px; border-radius: 9px; "
            f"font-size: {self._theme.size_small}px; font-weight: 600; "
            f"background: {with_alpha(background, 0.16)}; "
            f"border: 1px solid {with_alpha(foreground, 0.35)}; }}"
        )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class SearchBox(QLineEdit):
    """Filter input with a clear button; views connect ``textChanged``."""

    def __init__(self, placeholder: str = "Search...", parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setProperty("role", "search")
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(180)


# ---------------------------------------------------------------------------
# table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableColumn:
    key: str
    title: str
    width: int = 120
    stretch: bool = False
    format: Callable[[object], str] | None = None   # raw -> display text
    sort: Callable[[object], object] | None = None  # raw -> sort key


def _display(raw: object, column: TableColumn) -> str:
    if column.format is not None:
        return str(column.format(raw))
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    return str(raw)


def _sort_value(raw: object, column: TableColumn) -> object:
    if column.sort is not None:
        return column.sort(raw)
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return raw
    return _display(raw, column)


class TableModel(QAbstractTableModel):
    """Dict-row table model; rows are plain controller payloads."""

    def __init__(self, columns: Sequence[TableColumn], rows: Sequence[dict] = (),
                 parent: QObject | None = None):  # type: ignore[valid-type]
        super().__init__(parent)
        self.columns = list(columns)
        self.rows = list(rows)

    def set_rows(self, rows: Sequence[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row_at(self, source_row: int) -> dict | None:
        if 0 <= source_row < len(self.rows):
            return self.rows[source_row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return None
        column = self.columns[index.column()]
        raw = row.get(column.key)
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(raw, column)
        if role == Qt.ItemDataRole.UserRole:
            return _sort_value(raw, column)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].title
        return None


class TableFilterProxy(QSortFilterProxyModel):
    """Case-insensitive search across all columns plus exact column filters."""

    def __init__(self, columns: Sequence[TableColumn], parent: QObject | None = None):  # type: ignore[valid-type]
        super().__init__(parent)
        self._columns = list(columns)
        self._search = ""
        self._column_filters: dict[str, str] = {}
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_search(self, text: str) -> None:
        normalized = str(text).strip().lower()
        if normalized == self._search:
            return
        self._search = normalized
        self.invalidateFilter()

    def set_column_filter(self, key: str, value: str) -> None:
        normalized = str(value).strip().lower()
        current = self._column_filters.get(key, "")
        if normalized == current:
            return
        if normalized and normalized not in ("all", "any"):
            self._column_filters[key] = normalized
        else:
            self._column_filters.pop(key, None)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, TableModel):
            return True
        row = model.row_at(source_row)
        if row is None:
            return False
        for key, expected in self._column_filters.items():
            column = next((item for item in self._columns if item.key == key), None)
            if column is None:
                continue
            if _display(row.get(key), column).lower() != expected:
                return False
        if not self._search:
            return True
        return any(
            self._search in _display(row.get(column.key), column).lower()
            for column in self._columns
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        left_value = left.data(Qt.ItemDataRole.UserRole)
        right_value = right.data(Qt.ItemDataRole.UserRole)
        if (isinstance(left_value, (int, float)) and not isinstance(left_value, bool)
                and isinstance(right_value, (int, float)) and not isinstance(right_value, bool)):
            return left_value < right_value
        return str(left_value).lower() < str(right_value).lower()


class TablePanel(QWidget):
    """Searchable, sortable, selectable table bound to dict rows."""

    activated = Signal(object)          # row dict (double-click / Enter)
    selection_changed = Signal(object)  # row dict or None

    def __init__(self, columns: Sequence[TableColumn],
                 empty_message: str = "Nothing to show",
                 empty_detail: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._model = TableModel(columns, (), self)
        self._proxy = TableFilterProxy(columns, self)
        self._proxy.setSourceModel(self._model)

        self._stack = QStackedWidget(self)
        self._empty = EmptyState(empty_message, empty_detail)
        self._view = QTableView()
        self._view.setModel(self._proxy)
        self._view.setSortingEnabled(True)
        self._view.setShowGrid(False)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._view.verticalHeader().setVisible(False)
        self._view.verticalHeader().setDefaultSectionSize(30)
        self._view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self._view.horizontalHeader()
        header.setSectionsClickable(True)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        for index, column in enumerate(columns):
            self._view.setColumnWidth(index, column.width)
            if column.stretch:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        self._view.setWordWrap(False)
        self._view.setMinimumHeight(140)

        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._view.selectionModel().selectionChanged.connect(self._on_selection)
        self._view.doubleClicked.connect(self._on_double_clicked)
        self._proxy.rowsInserted.connect(self._sync_empty)
        self._proxy.modelReset.connect(self._sync_empty)
        self._sync_empty()

    # -- data ---------------------------------------------------------
    @property
    def model(self) -> TableModel:
        return self._model

    @property
    def proxy(self) -> TableFilterProxy:
        return self._proxy

    @property
    def view(self) -> QTableView:
        return self._view

    def set_rows(self, rows: Sequence[dict]) -> None:
        self._model.set_rows(rows)
        self._sync_empty()

    def rows(self) -> list[dict]:
        return list(self._model.rows)

    def set_filter(self, text: str) -> None:
        self._proxy.set_search(text)
        self._sync_empty()

    def set_column_filter(self, key: str, value: str) -> None:
        self._proxy.set_column_filter(key, value)
        self._sync_empty()

    def set_empty_state(self, heading: str, detail: str = "") -> None:
        self._empty.set_state(heading, detail)

    # -- selection ----------------------------------------------------
    def selected_row(self) -> dict | None:
        indexes = self._view.selectionModel().selectedRows()
        if not indexes:
            return None
        source = self._proxy.mapToSource(indexes[0])
        return self._model.row_at(source.row())

    def row_of(self, index: QModelIndex) -> dict | None:
        if not index.isValid():
            return None
        return self._model.row_at(self._proxy.mapToSource(index).row())

    def select_row_index(self, index: int) -> None:
        """Select a visible (sorted/filtered) row by display index."""
        if index < 0 or index >= self._proxy.rowCount():
            return
        proxy_index = self._proxy.index(index, 0)
        self._view.selectRow(proxy_index.row())

    def focus_table(self) -> None:
        self._view.setFocus()

    def _on_selection(self, *_args: object) -> None:
        self.selection_changed.emit(self.selected_row())

    def _on_double_clicked(self, index: QModelIndex) -> None:
        row = self.row_of(index)
        if row is not None:
            self.activated.emit(row)

    def _sync_empty(self, *_args: object) -> None:
        has_rows = self._proxy.rowCount() > 0
        self._stack.setCurrentWidget(self._view if has_rows else self._empty)


# ---------------------------------------------------------------------------
# detail helpers
# ---------------------------------------------------------------------------

class KeyValueGrid(QWidget):
    """Two-column label/value grid for detail panels."""

    def __init__(self, parent: QWidget | None = None, columns: int = 2):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(DARK.space_sm)
        self._grid = QHBoxLayout()
        self._grid.setSpacing(DARK.space_xl)
        self._columns = columns
        self._slots: list[QVBoxLayout] = []
        self._row_counts: list[int] = []
        for _ in range(columns):
            column = QVBoxLayout()
            column.setSpacing(DARK.space_sm)
            self._slots.append(column)
            self._row_counts.append(0)
            self._grid.addLayout(column)
        self._layout.addLayout(self._grid)
        self._extra = QVBoxLayout()
        self._extra.setSpacing(DARK.space_sm)
        self._layout.addLayout(self._extra)

    def add_row(self, name: str, value: object, mono_value: bool = False) -> None:
        text = "-" if value is None or value == "" else str(value)
        block = QVBoxLayout()
        block.setSpacing(0)
        name_label = QLabel(name)
        name_label.setProperty("role", "faint")
        block.addWidget(name_label)
        value_label = QLabel(text)
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if mono_value:
            value_label.setProperty("role", "mono")
        block.addWidget(value_label)
        # round-robin across columns, balancing on actual row counts
        column_index = min(range(self._columns), key=self._row_counts.__getitem__)
        self._slots[column_index].addLayout(block)
        self._row_counts[column_index] += 1

    def add_widget(self, widget: QWidget) -> None:
        self._extra.addWidget(widget)

    def clear(self) -> None:
        """Remove all rows (keeps the column structure intact)."""
        for slot in self._slots:
            _clear_layout(slot)
        _clear_layout(self._extra)
        self._row_counts = [0] * self._columns


def _clear_layout(layout) -> None:  # type: ignore[no-untyped-def]
    while layout.count():
        child = layout.takeAt(0)
        widget = child.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif child.layout() is not None:
            _clear_layout(child.layout())


class LogText(QPlainTextEdit):
    """Read-only monospace log/report viewer."""

    def __init__(self, parent: QWidget | None = None, theme: Theme = DARK):
        super().__init__(parent)
        font = QFont(theme.font_mono)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMinimumHeight(120)

    def set_log(self, text: str) -> None:
        self.setPlainText(text)

    def append_log(self, text: str) -> None:
        self.appendPlainText(text)


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

class _PipelineStage(QWidget):
    def __init__(self, stage: dict, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self._key = str(stage.get("key", ""))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(theme.space_sm)
        self.dot = PulsingDot(size=10, parent=self)
        top.addWidget(self.dot)
        top.addStretch(1)
        title = QLabel(str(stage.get("label", "")))
        title.setProperty("role", "title")
        top.addWidget(title)
        layout.addLayout(top)
        self.subtitle = QLabel(str(stage.get("subtitle", "") or ""))
        self.subtitle.setProperty("role", "subtitle")
        layout.addWidget(self.subtitle)
        self.set_status(stage.get("status"))

    @property
    def key(self) -> str:
        return self._key

    def set_status(self, status: object) -> None:
        foreground, _background = status_colors(status, self._theme)
        self.dot.set_color(foreground)
        self.dot.set_pulsing(str(getattr(status, "value", status) or "") == "running")

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)


class PipelineBar(QWidget):
    """Horizontal stage strip: Plan -> Implement -> Verify -> Review (or a
    custom DAG's task order), colored by each task's status."""

    def __init__(self, theme: Theme = DARK, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.space_sm)
        self._stages: list[_PipelineStage] = []

    def set_stages(self, stages: Sequence[dict]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._stages = []
        for index, stage in enumerate(stages):
            if index:
                connector = QFrame()
                connector.setFrameShape(QFrame.Shape.HLine)
                connector.setStyleSheet(
                    f"background: {self._theme.border}; max-height: 1px; border: none;"
                )
                connector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                connector.setMinimumWidth(self._theme.space_lg)
                self._layout.addWidget(connector, stretch=1)
            stage_widget = _PipelineStage(stage, self._theme)
            self._stages.append(stage_widget)
            self._layout.addWidget(stage_widget)
        if stages:
            self._layout.addStretch(1)

    def stage_keys(self) -> list[str]:
        return [stage.key for stage in self._stages]


# ---------------------------------------------------------------------------
# command list (used by the command palette)
# ---------------------------------------------------------------------------

class CommandList(QListWidget):
    """Filtered list of commands with keyboard navigation."""

    chosen = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlternatingRowColors(False)

    def set_commands(self, entries: Sequence[tuple[str, str]]) -> None:
        """``entries``: (display text, shortcut hint) pairs."""
        self.clear()
        for text, shortcut in entries:
            item = QListWidgetItem(text)
            if shortcut:
                item.setToolTip(shortcut)
            self.addItem(item)
        if self.count():
            self.setCurrentRow(0)

    def current_index(self) -> int:
        return self.currentRow()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.currentRow() >= 0:
                self.chosen.emit(self.currentRow())
                return
        if event.key() == Qt.Key.Key_Down and self.currentRow() + 1 < self.count():
            self.setCurrentRow(self.currentRow() + 1)
            return
        if event.key() == Qt.Key.Key_Up and self.currentRow() > 0:
            self.setCurrentRow(self.currentRow() - 1)
            return
        super().keyPressEvent(event)
