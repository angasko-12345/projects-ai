"""Workspace right pane: Diff / Logs / Records (Phase C3).

480px wide, 420 below a 1280px window. Diff shows ``workflow_diff`` for the
open workflow with an attempt picker; Logs lists the picked attempt's task
logs (``list_logs``) and tails one (``read_log``); Records hosts the control
center's record tabs unchanged. Reads go through the view context's bridge
and run only while their tab is on screen.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .components import IconButton
from .context import AsyncMixin, ViewContext
from .diffview import DiffView
from .icons import qicon
from .tokens import DARK as _T
from .transcript import TextLabel
from .widgets import LogText

PANE_WIDE, PANE_NARROW, NARROW_BELOW = 480, 420, 1280
TABS = (
    ("diff", "Diff", "file-diff"),
    ("logs", "Logs", "terminal"),
    ("records", "Records", "list-checks"),
)
_SOURCE = {"worktree": "live worktree", "branch": "branch", "stat": "recorded stat"}
_STYLE = """
QFrame#RightPane {{ background: {t.bg}; border-left: 1px solid {t.border}; }}
QToolButton[paneTab=true] {{ color: {t.text_muted}; border: 1px solid transparent;
    border-radius: 6px; padding: 3px 8px; }}
QToolButton[paneTab=true]:checked {{ color: {t.text}; background: {t.surface};
    border-color: {t.border_strong}; }}
QPlainTextEdit {{ border: none; }}
"""


def diff_choices(blocks: object) -> list[dict]:
    """Picker rows: the attempts that recorded changes, then the newest live
    one (or the newest attempt when nothing changed). The last row owns the
    live ``workflow_diff``; earlier rows show their recorded stat."""
    attempts = [a for b in blocks or () for a in b.get("attempts") or () if isinstance(a, dict)]
    rows = [a for a in attempts if a.get("diff") or (a.get("run") or {}).get("files_changed")]
    live = [a for a in attempts if a.get("live")]
    if live and all(row is not live[-1] for row in rows):
        rows.append(live[-1])
    return rows or attempts[-1:]


def _recorded(run: dict) -> str:
    return str(run.get("diff_stat") or "").strip() or "\n".join(run.get("files_changed") or ())


class RightPane(QFrame, AsyncMixin):
    """Toggleable third pane of the workspace; ``closed`` fires from its X."""

    closed = Signal()

    def __init__(self, context: ViewContext, records: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = context
        self._init_async(context.bridge)
        self._target: tuple[str, str] = ("", "")
        self._choices: list[dict] = []
        self._source = "worktree"
        self.setObjectName("RightPane")
        self.setStyleSheet(_STYLE.format(t=_T))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 0, 0, 0)
        layout.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(_T.space_md, _T.space_md, _T.space_md, _T.space_md)
        self._buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        for key, text, icon_name in TABS:
            button = self._buttons[key] = QToolButton()
            button.setProperty("paneTab", True)
            button.setText(text)
            button.setIcon(qicon(icon_name, _T.text_muted, 14, checked_color=_T.text))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, key=key: self.set_tab(key))
            group.addButton(button)
            bar.addWidget(button)
        bar.addStretch(1)
        close = IconButton("x", "Close pane", size=28, icon_size=16)
        close.clicked.connect(lambda _c=False: self.closed.emit())
        bar.addWidget(close)
        layout.addLayout(bar)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._diff = DiffView(theme=_T)
        self._attempt, row = self._page("Attempt", self._diff)
        self._added = TextLabel("", _T.size_caption, _T.success)
        self._removed = TextLabel("", _T.size_caption, _T.danger)
        for label in (self._added, self._removed):
            label.setWordWrap(False)
            row.addWidget(label)
        self._log = LogText()
        self._log_pick, _row = self._page("Log file", self._log)
        self._attempt.activated.connect(lambda _i: self._load())
        self._log_pick.activated.connect(lambda _i: self._read_log())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(records)
        self._stack.addWidget(scroll)
        self.set_tab("diff")

    def _page(self, name: str, body: QWidget) -> tuple[QComboBox, QHBoxLayout]:
        """A stack page: a picker row over ``body``."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(_T.space_md, 0, _T.space_md, _T.space_sm)
        picker = QComboBox()
        picker.setAccessibleName(name)
        picker.setProperty("scope", True)
        picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        row.addWidget(picker)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(body, 1)
        self._stack.addWidget(page)
        return picker, row

    # -- state in ----------------------------------------------------------
    def tab(self) -> str:
        return TABS[self._stack.currentIndex()][0]

    def set_tab(self, key: str) -> None:
        index = next((i for i, tab in enumerate(TABS) if tab[0] == key), 0)
        self._buttons[TABS[index][0]].setChecked(True)
        self._stack.setCurrentIndex(index)
        self._load()

    def fit_width(self, window_width: int) -> None:
        self.setFixedWidth(PANE_WIDE if window_width >= NARROW_BELOW else PANE_NARROW)

    def set_workflow(self, repository: str, workflow_id: str, blocks: object) -> None:
        """Follow the open workflow; keeps the picked attempt across polls."""
        picked = self._picked().get("id")
        moved = (repository, workflow_id) != self._target
        self._target = (repository, workflow_id)
        self._choices = diff_choices(blocks)
        self._attempt.clear()
        for choice in self._choices:
            self._attempt.addItem(self._label(choice), choice.get("id"))
        if not self._choices:
            self._attempt.addItem("No attempts yet", "")
        found = self._attempt.findData(picked) if picked and not moved else -1
        self._attempt.setCurrentIndex(found if found >= 0 else self._attempt.count() - 1)
        self._load()

    def show_attempt(self, run_id: str) -> None:
        """ "Open diff" on a transcript attempt: the Diff tab on that attempt."""
        found = self._attempt.findData(run_id)
        if found >= 0:
            self._attempt.setCurrentIndex(found)
        self.set_tab("diff")

    def _label(self, choice: dict) -> str:
        title = str((choice.get("text") or {}).get("title") or "Attempt")
        live = choice is self._choices[-1]
        return f"{title} · {_SOURCE.get(self._source, self._source) if live else 'recorded stat'}"

    def _picked(self) -> dict:
        index = self._attempt.currentIndex()
        return self._choices[index] if 0 <= index < len(self._choices) else {}

    # -- reads ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._load()

    def _load(self) -> None:
        repository, workflow_id = self._target
        if not workflow_id or not self.isVisible() or self.tab() == "records":
            return
        picked = self._picked()
        if self.tab() == "logs":
            self._load_logs(repository, picked.get("run") or {})
        elif picked and picked is not self._choices[-1]:  # history: its recorded stat
            self._show_diff({"source": "", "text": _recorded(picked.get("run") or {})})
        else:
            self.submit(
                "pane-diff",
                lambda: self.ctx.controller.workflow_diff(repository, workflow_id),
                self._on_diff,
                lambda message: self._show_diff({"text": f"Diff unavailable: {message}"}),
            )

    def _on_diff(self, payload: object) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self._source = str(payload.get("source") or "worktree")
        if self._choices:
            self._attempt.setItemText(len(self._choices) - 1, self._label(self._choices[-1]))
            if self._picked() is not self._choices[-1]:
                return  # an older attempt was picked meanwhile
        self._show_diff(payload)

    def _show_diff(self, payload: dict) -> None:
        self._diff.set_payload(payload)
        files = self._diff.files()
        self._added.setText(f"+{sum(f.added for f in files)}" if files else "")
        self._removed.setText(f"\u2212{sum(f.removed for f in files)}" if files else "")

    def _load_logs(self, repository: str, run: dict) -> None:
        def listed(entries: object) -> None:
            self._log_pick.clear()
            for entry in entries if isinstance(entries, list) else ():
                if isinstance(entry, dict) and entry.get("name"):
                    self._log_pick.addItem(str(entry["name"]).rsplit("/", 1)[-1], entry["name"])
                    if entry.get("path") and entry.get("path") == run.get("stdout_path"):
                        self._log_pick.setCurrentIndex(self._log_pick.count() - 1)
            self._log_pick.setVisible(self._log_pick.count() > 0)
            self._log.set_log("")
            self._log.setPlaceholderText("No logs recorded for this attempt yet.")
            self._read_log()

        task_id = str(run.get("task_id") or "")
        if not task_id:
            return listed([])
        self.submit(
            "pane-logs",
            lambda: self.ctx.controller.list_logs(repository, task_id),
            listed,
            lambda message: self._log.set_log(f"Logs unavailable: {message}"),
        )

    def _read_log(self) -> None:
        name, repository = self._log_pick.currentData(), self._target[0]

        def shown(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            older = "[older output not shown]\n" if data.get("truncated") else ""
            self._log.set_log(older + str(data.get("text") or ""))
            self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

        if name:
            self.submit(
                "pane-log",
                lambda: self.ctx.controller.read_log(repository, name),
                shown,
                lambda message: self._log.set_log(f"Log unavailable: {message}"),
            )
