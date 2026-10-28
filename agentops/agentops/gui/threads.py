"""Workspace thread list: workflows grouped by recent repository.

One painted ``QListView`` (model + delegate, no per-row widgets) so a long
settled history stays cheap. Rows come from :func:`derive.thread_items`; the
active repository is fed by ``WorkflowsView.refresh`` at its usual cadence,
and the other recent repositories are polled here every 30s, only when their
``<repo>/.agentops/state.sqlite`` already exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from .components import glyph_name
from .context import AsyncMixin, ViewContext
from .derive import pollable_repositories, thread_items
from .icons import icon
from .tokens import DARK, qcolor, qfont, status_color

ROW_ROLE = Qt.ItemDataRole.UserRole + 1
OTHERS_POLL_MS = 30_000
_HEIGHT = {"group": 30, "thread": 72, "settled": 32, "empty": 32}


def repo_label(path: str) -> str:
    return Path(path.replace("\\", "/").rstrip("/")).name or path


def _row(kind: str, repo: str, text: str, closed: bool = False, **extra) -> dict:
    chevron = "chevron-right" if closed else "chevron-down"
    return {"kind": kind, "repo": repo, "text": text, "chevron": chevron, **extra}


def load_threads(controller: object, repository: str) -> dict:
    """The three bounded reads behind one repository's threads (worker thread)."""
    page = controller.list_workflows(repository, limit=50)
    page = page if isinstance(page, dict) else {}
    workflows = [row for row in page.get("workflows") or [] if isinstance(row, dict)]
    joins: dict[str, list] = {}
    for key, read in (
        ("refs", lambda: controller.list_worktree_refs(repository)),
        ("runs", lambda: controller.list_recent_agent_runs(repository, limit=100)),
    ):
        try:  # a failed join leaves branch/agent blank; the list still shows
            joins[key] = [row for row in read() or [] if isinstance(row, dict)]
        except Exception:  # noqa: BLE001
            joins[key] = []
    return {"workflows": workflows, **joins}


class ThreadModel(QAbstractListModel):
    """Flat rows: a group header per repository, its active threads, then a
    "Settled (n)" toggle row (its threads follow only while expanded)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.current: tuple[str, str] = ("", "")

    def rowCount(self, parent=None) -> int:  # noqa: N802 - Qt API
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get("title") or row.get("text") or ""
        if role == Qt.ItemDataRole.ToolTipRole and row["kind"] == "thread":
            return row.get("title") or None
        return None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class ThreadDelegate(QStyledItemDelegate):
    """Paints thread rows (three lines), group headers and settled toggles."""

    def __init__(self, model: ThreadModel, parent=None, theme=DARK):
        super().__init__(parent)
        self._model = model
        self._t = theme

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        row = index.data(ROW_ROLE) or {}
        return QSize(option.rect.width(), _HEIGHT.get(row.get("kind"), 32))

    def _font(self, px: int, weight=QFont.Weight.Normal) -> QFont:
        font = qfont(self._t, px)
        font.setWeight(weight)
        return font

    def _text(self, p, rect, text, font, color, align=Qt.AlignmentFlag.AlignLeft) -> int:
        """Elided single line, vertically centred in ``rect``; returns its width."""
        p.setFont(font)
        p.setPen(qcolor(color))
        shown = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        p.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), shown)
        return QFontMetrics(font).horizontalAdvance(shown)

    def _icon(self, p, name, color, x, y, size) -> None:
        p.drawPixmap(x, y, icon(name, color, size, p.device().devicePixelRatioF()))

    def _from(self, p, r: QRect, x: int, text: str, font: QFont, color: str) -> int:
        """Text from ``x`` to the row's right edge; returns where it ends."""
        return x + self._text(
            p, QRect(x, r.top(), r.right() - x - 30, r.height()), text, font, color
        )

    def paint(self, p: QPainter, option, index) -> None:
        row = index.data(ROW_ROLE) or {}
        t, r = self._t, option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        kind = row.get("kind")
        caption = self._font(t.size_caption)
        if kind == "thread":
            current = (row["repo"], row["id"]) == self._model.current
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            if current or hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor(t.selected if current else t.surface_hover))
                p.drawRoundedRect(r.adjusted(0, 1, 0, -1), t.radius_md, t.radius_md)
            self._paint_thread(p, r.adjusted(t.space_md, 9, -t.space_md, -9), row, caption)
        elif kind == "group":
            self._icon(p, row["chevron"], t.text_faint, r.left() + 8, r.center().y() - 6, 12)
            bold = self._font(t.size_caption, QFont.Weight.DemiBold)
            x = self._from(p, r, r.left() + 26, row["text"], bold, t.text_muted)
            self._from(p, r, x + t.space_sm, str(row["count"]), caption, t.text_faint)
        elif kind == "settled":
            x = self._from(p, r, r.left() + t.space_md, row["text"], caption, t.text_faint)
            x += t.space_sm
            p.setPen(qcolor(t.border_strong))
            p.drawLine(x, r.center().y(), r.right() - 30, r.center().y())
            self._icon(p, row["chevron"], t.text_faint, r.right() - 22, r.center().y() - 6, 12)
        else:
            self._from(p, r, r.left() + t.space_md, row.get("text", ""), caption, t.text_muted)
        p.restore()

    def _paint_thread(self, p, box: QRect, row: dict, caption: QFont) -> None:
        t = self._t
        line = (box.height() - 8) // 3
        top, mid, bottom = (
            QRect(box.left(), box.top() + i * (line + 4), box.width(), line) for i in range(3)
        )
        status = row["status"]
        self._icon(
            p, glyph_name(status), status_color(status, t), top.left(), top.center().y() - 7, 14
        )
        age_color = t.accent if status == "running" else t.text_muted
        age_w = self._text(p, top, row["age"], caption, age_color, Qt.AlignmentFlag.AlignRight)
        meta = f"{row['agent'] or 'unassigned'} · {row['role'] or status}"
        self._text(p, top.adjusted(20, 0, -age_w - 8, 0), meta, caption, t.text_muted)
        title = row["title"] or "(no description)"
        self._text(p, mid, title, self._font(t.size_prose, QFont.Weight.DemiBold), t.text)
        right = Qt.AlignmentFlag.AlignRight
        tasks_w = self._text(p, bottom, f"{row['progress']} tasks", caption, t.text_muted, right)
        rest = bottom.adjusted(0, 0, -tasks_w - 8, 0)
        if row["branch"]:
            self._icon(p, "git-branch", t.text_faint, rest.left(), rest.center().y() - 6, 12)
            rest.setLeft(rest.left() + 17)
        self._text(p, rest, row["branch"] or "no worktree yet", caption, t.text_muted)


class ThreadList(QWidget, AsyncMixin):
    """Grouped, painted list of workflows. Emits :attr:`workflow_selected`
    with ``(repository, workflow_id)`` when a thread is clicked or activated."""

    workflow_selected = Signal(str, str)

    def __init__(self, context: ViewContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)
        self._groups: dict[str, tuple[list[dict], list[dict]]] = {}
        self._expanded: set[str] = set()
        self._folded: set[str] = set()
        self._filter: tuple[str, str] = ("", "")
        self.errors: dict[str, str] = {}  # repository -> last read error
        self.model = ThreadModel(self)
        self.view = QListView()
        self.view.setObjectName("ThreadList")
        self.view.setModel(self.model)
        self.view.setItemDelegate(ThreadDelegate(self.model, self.view))
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.setStyleSheet("QListView#ThreadList { background: transparent; border: none; }")
        self.view.clicked.connect(self._on_click)
        self.view.activated.connect(self._on_click)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self._timer = QTimer(self)
        self._timer.setInterval(OTHERS_POLL_MS)
        self._timer.timeout.connect(self.poll_others)

    # -- data in ---------------------------------------------------------
    def set_repository_data(self, repository: str, data: object, now: datetime | None = None):
        """Feed one repository's ``load_threads`` result (or drop it on None)."""
        if not isinstance(data, dict):
            self._groups.pop(repository, None)
        else:
            now = now or datetime.now(timezone.utc)
            self._groups[repository] = thread_items(
                data.get("workflows") or [], data.get("refs") or [], data.get("runs") or [], now
            )
        self._rebuild()

    def set_current(self, repository: str, workflow_id: str) -> None:
        self.model.current = (repository, str(workflow_id or ""))
        self.view.viewport().update()

    def set_filter(self, text: str, status: str) -> None:
        self._filter = (text.strip().lower(), (status or "").lower())
        self._rebuild()

    def poll_others(self) -> None:
        """Read the other recent repositories that already have a state DB."""
        active = self.ctx.repository()
        pinned = getattr(self.ctx.controller, "state_path", None) is not None
        others = pollable_repositories(self.ctx.settings().recent_repositories, active, pinned)
        for stale in [repo for repo in self._groups if repo != active and repo not in others]:
            self._groups.pop(stale)
        self._rebuild()
        controller = self.ctx.controller
        for repo in others:
            self.submit(
                f"threads:{repo}",
                lambda repo=repo: load_threads(controller, repo),
                lambda data, repo=repo: self.set_repository_data(repo, data),
                lambda message, repo=repo: self._on_repo_error(repo, message),
            )

    def _on_repo_error(self, repository: str, message: str) -> None:
        """An unreadable recent repo drops out of the list; the reason is kept."""
        self.errors[repository] = message
        self.set_repository_data(repository, None)

    # -- rows ------------------------------------------------------------
    def _matches(self, item: dict) -> bool:
        text, status = self._filter
        return (not status or item["status"] == status) and (
            not text or text in f"{item['title']} {item['id']} {item['branch']}".lower()
        )

    def _rebuild(self) -> None:
        active = self.ctx.repository()
        order = [active, *self.ctx.settings().recent_repositories]
        filtering = any(self._filter)
        rows: list[dict] = []
        for repo in dict.fromkeys(path for path in order if path):
            if repo not in self._groups:
                continue
            live, settled = self._groups[repo]
            live = [item for item in live if self._matches(item)]
            settled = [item for item in settled if self._matches(item)]
            if repo != active and not (live or settled):
                continue
            folded = repo in self._folded
            rows.append(_row("group", repo, repo_label(repo), folded, count=len(live)))
            if folded:
                continue
            rows += [{"kind": "thread", "repo": repo, **item} for item in live]
            if not (live or settled):
                empty = "No matching workflows" if filtering else "No workflows yet"
                rows.append(_row("empty", repo, empty))
            if settled:
                opened = filtering or repo in self._expanded
                rows.append(_row("settled", repo, f"Settled ({len(settled)})", not opened))
                if opened:
                    rows += [{"kind": "thread", "repo": repo, **item} for item in settled]
        self.model.set_rows(rows)

    def _on_click(self, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE) or {}
        kind, repo = row.get("kind"), row.get("repo", "")
        if kind == "thread":
            self.set_current(repo, row["id"])
            self.workflow_selected.emit(repo, row["id"])
        elif kind in ("settled", "group"):
            toggled = self._expanded if kind == "settled" else self._folded
            toggled.symmetric_difference_update({repo})
            self._rebuild()

    # -- lifecycle ---------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self.poll_others()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().hideEvent(event)
        self._timer.stop()
