"""Shared list + detail scaffolding for Runs/Verification/Failures/Artifacts
and Worktrees.

Subclasses supply the columns, the async load, and how a selected row fills
the detail panel; this base owns toolbar/search, filtering, selection memory
across refreshes, and the count label.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QSplitter, QVBoxLayout

from ..detail import DetailPanel
from ..widgets import SearchBox, TablePanel, TableColumn, faint
from .base import BaseView


class ListDetailView(BaseView):
    """Toolbar + table + optional detail panel with selection memory."""

    view_id: str = ""
    title: str = ""
    columns: tuple[TableColumn, ...] = ()
    empty_heading: str = "Nothing to show"
    empty_detail: str = ""
    search_placeholder: str = "Search..."

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._search = SearchBox(self.search_placeholder)
        self._search.textChanged.connect(self._apply_filters)
        toolbar.addWidget(self._search, stretch=1)
        self._extend_toolbar(toolbar)
        self._count = faint("")
        toolbar.addWidget(self._count)
        root.addLayout(toolbar)

        splitter = QSplitter()
        self._table = TablePanel(self.columns, self.empty_heading, self.empty_detail)
        splitter.addWidget(self._table)
        self._detail = self._build_detail()
        if self._detail is not None:
            splitter.addWidget(self._detail)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

        self._table.selection_changed.connect(self._on_pick)
        self._table.activated.connect(self._on_pick)
        self._table.proxy.layoutChanged.connect(self._update_count)

        self._rows: list[dict] = []
        self._selected_id: str | None = None
        self._build_hook()
        if self._detail is not None:
            self.fill_detail(None)

    # -- subclass hooks ------------------------------------------------
    def _build_detail(self) -> DetailPanel | None:
        """Return the detail panel for this surface (or None for tables only)."""
        return None

    def _extend_toolbar(self, toolbar: QHBoxLayout) -> None:
        """Add filters/actions between the search box and the count label."""

    def _build_hook(self) -> None:
        """Wire extra signals after the widget tree exists."""

    def _load(self) -> list[dict]:
        """Fetch rows; always called on a worker thread via ``submit``."""
        raise NotImplementedError

    def fill_detail(self, row: dict | None) -> None:
        """Push the selected row into the detail panel (None = clear)."""

    def _fetch_one(self, repository: str, item_id: str) -> dict | None:
        """Optional direct load for ``show_item`` when the id is off-page."""
        return None

    # -- data ----------------------------------------------------------
    def refresh(self) -> None:
        repository = self.repository()
        if not repository:
            self._set_rows([])
            if self._detail is not None:
                self.fill_detail(None)
            return
        self.submit("rows", self._load, self._on_rows,
                    lambda message: self.ctx.toast("Load failed", message, "error"))

    def _set_rows(self, rows: list[dict]) -> None:
        self._rows = rows
        self._table.set_rows(rows)
        self._update_count()

    def _on_rows(self, rows: object) -> None:
        self._set_rows(list(rows or []))  # type: ignore[arg-type]
        if self._selected_id:
            self._reselect(self._selected_id)

    # -- selection -----------------------------------------------------
    def _on_pick(self, row: object) -> None:
        if isinstance(row, dict):
            self._selected_id = str(row.get("id") or "") or None
            self.fill_detail(row)
        else:
            self._selected_id = None
            if self._detail is not None:
                self.fill_detail(None)

    def _reselect(self, item_id: str) -> None:
        proxy = self._table.proxy
        for index in range(proxy.rowCount()):
            record = self._table.row_of(proxy.index(index, 0))
            if record and str(record.get("id") or "") == item_id:
                self._table.select_row_index(index)
                return
        self._selected_id = None
        if self._detail is not None:
            self.fill_detail(None)

    def show_item(self, item_id: str) -> None:
        """Select ``item_id``; fetch it directly when outside the loaded page."""
        item_id = str(item_id or "")
        if not item_id:
            return
        proxy = self._table.proxy
        for index in range(proxy.rowCount()):
            record = self._table.row_of(proxy.index(index, 0))
            if record and str(record.get("id") or "") == item_id:
                self._table.select_row_index(index)
                return
        repository = self.repository()

        def load() -> object:
            return self._fetch_one(repository, item_id)

        def on_one(value: object) -> None:
            if isinstance(value, dict):
                self._selected_id = str(value.get("id") or item_id)
                if self._detail is not None:
                    self.fill_detail(value)
            else:
                self.ctx.toast("Not found", item_id, "warning")

        self.submit("show-item", load, on_one)

    # -- filters -------------------------------------------------------
    def _apply_filters(self, *_args: object) -> None:
        self._table.set_filter(self._search.text())
        self._update_count()

    def _update_count(self, *_args: object) -> None:
        visible = self._table.proxy.rowCount()
        total = len(self._rows)
        if total == visible:
            self._count.setText(f"{total} rows")
        else:
            self._count.setText(f"{visible} of {total} rows")
