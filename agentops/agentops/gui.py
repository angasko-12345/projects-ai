"""Tkinter desktop client for the local AgentOps service.

The GUI is intentionally a thin presentation and lifecycle layer. Agent
discovery, process execution, Git worktree isolation, persistence, and
verification remain owned by the existing AgentOps modules. Long-running work
executes in controller-owned background threads; all Tk widget updates are
marshalled back onto the Tk event loop.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Protocol

from . import __version__
from .gui_controller import AgentOpsController, EventCallback


class GuiController(Protocol):
    def detect_agents(self) -> dict[str, object]: ...
    def run_agent(self, agent: str, prompt: str, directory: str | Path, callback: EventCallback) -> None: ...
    def run_task(self, description: str, directory: str | Path, callback: EventCallback) -> None: ...
    def cancel(self) -> None: ...
    def list_workflows(self, directory: str | Path, limit: int = 25, offset: int = 0,
                       status: str | None = None) -> dict[str, object]: ...
    def get_workflow(self, directory: str | Path, workflow_id: str) -> dict[str, object] | None: ...
    def list_agent_runs(self, directory: str | Path, workflow_id: str | None = None,
                        task_id: str | None = None, status: str | None = None,
                        limit: int = 50, offset: int = 0) -> list[dict[str, object]]: ...
    def get_agent_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None: ...
    def list_verification_runs(self, directory: str | Path, workflow_id: str | None = None,
                               task_id: str | None = None, limit: int = 50,
                               offset: int = 0) -> list[dict[str, object]]: ...
    def get_verification_run(self, directory: str | Path, run_id: str) -> dict[str, object] | None: ...
    def list_failures(self, directory: str | Path, workflow_id: str | None = None,
                      task_id: str | None = None, limit: int = 50,
                      offset: int = 0) -> list[dict[str, object]]: ...
    def list_logs(self, directory: str | Path, task_id: str | None = None,
                  limit: int = 200) -> list[dict[str, object]]: ...
    def read_log(self, directory: str | Path, name: str | Path, max_bytes: int = 65536) -> dict[str, object]: ...
    def list_artifacts(self, directory: str | Path, workflow_id: str | None = None,
                       task_id: str | None = None, kind: str | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict[str, object]]: ...
    def read_artifact(self, directory: str | Path, artifact_id: str,
                      max_bytes: int = 65536) -> dict[str, object]: ...
    def list_worktrees(self, directory: str | Path) -> list[dict[str, object]]: ...
    def inspect_worktree(self, directory: str | Path, path: str | Path) -> dict[str, object]: ...
    def cleanup_worktree(self, directory: str | Path, path: str | Path,
                         delete_unmerged_branch: bool = False) -> dict[str, object]: ...
    def retry_merge(self, directory: str | Path, path: str | Path) -> dict[str, object]: ...
    def latest_workflow(self, directory: str | Path) -> dict[str, object] | None: ...


def _task_value(task: object, name: str, default: str = "") -> str:
    if isinstance(task, dict):
        value = task.get(name, default)
    else:
        value = getattr(task, name, default)
    return "" if value is None else str(value)


def _status_text(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value).upper()


def _short_text(value: str, length: int = 120) -> str:
    text = " ".join(value.split())
    return text if len(text) <= length else text[: length - 3].rstrip() + "..."


class AgentOpsApp:
    """Main Tkinter application."""

    def __init__(self, root: tk.Tk | None = None, controller: GuiController | None = None):
        self.root = root or tk.Tk()
        self.controller: GuiController = controller or AgentOpsController()
        self._operation_controller: GuiController | None = None
        self._operation_seq = 0
        self._operation_id = 0
        self._config_controller: AgentOpsController | None = None
        self._config_controller_path = ""
        self._running = False
        self._status_poll_id: str | None = None
        self._last_workflow_id: str | None = None
        self._current_tasks: list[object] = []
        self._history_limit = 25
        self._history_offset = 0
        self._history_total = 0
        self._history_rows: list[dict[str, object]] = []
        self.history_filter = tk.StringVar(value="all")
        self._log_entries: list[dict[str, object]] = []
        self.log_task = tk.StringVar()
        self._artifact_entries: list[dict[str, object]] = []
        self.artifact_task = tk.StringVar()
        self._worktree_entries: list[dict[str, object]] = []
        self.delete_unmerged_branch = tk.BooleanVar(value=False)
        self.repository = tk.StringVar(value=str(Path.cwd()))
        self.config_path = tk.StringVar()
        self.agent_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_agents()
        self.refresh_status()

    def _build_ui(self) -> None:
        self.root.title(f"AgentOps {__version__}")
        self.root.geometry("1060x740")
        self.root.minsize(840, 540)
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        # Vertical budget: the notebook, status, and output frames share
        # all extra space (previously only the notebook grew, and it still
        # starved: fixed-height children claimed the 700px window first,
        # leaving the notebook ~100px tall and its text boxes unmapped).
        main.rowconfigure(4, weight=2)
        main.rowconfigure(5, weight=1)
        main.rowconfigure(6, weight=1)

        ttk.Label(main, text="AgentOps", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(main, text="Local coding-agent orchestration").grid(
            row=1, column=0, columnspan=4, sticky="w")
        ttk.Label(main, text="Repository:").grid(row=2, column=0, sticky="w", pady=(10, 2))
        self.repository_entry = ttk.Entry(main, textvariable=self.repository, width=72)
        self.repository_entry.grid(row=2, column=1, sticky="ew", pady=(10, 2))
        self.browse_repository_button = ttk.Button(main, text="Browse...", command=self.choose_repository)
        self.browse_repository_button.grid(row=2, column=2, padx=(8, 0), pady=(10, 2))
        self.refresh_button = ttk.Button(main, text="Refresh", command=self.refresh_all)
        self.refresh_button.grid(row=2, column=3, padx=(8, 0), pady=(10, 2))
        ttk.Label(main, text="Config (optional):").grid(row=3, column=0, sticky="w")
        self.config_entry = ttk.Entry(main, textvariable=self.config_path, width=72)
        self.config_entry.grid(row=3, column=1, sticky="ew")
        self.browse_config_button = ttk.Button(main, text="Browse...", command=self.choose_config)
        self.browse_config_button.grid(row=3, column=2, padx=(8, 0))
        self.detect_button = ttk.Button(main, text="Detect agents", command=self.refresh_agents)
        self.detect_button.grid(row=3, column=3, padx=(8, 0))

        notebook = ttk.Notebook(main)
        notebook.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(12, 8))
        run_tab = ttk.Frame(notebook, padding=10)
        task_tab = ttk.Frame(notebook, padding=10)
        notebook.add(run_tab, text="Direct run")
        notebook.add(task_tab, text="Task workflow")
        run_tab.columnconfigure(1, weight=1)
        run_tab.rowconfigure(1, weight=1)
        task_tab.columnconfigure(1, weight=1)
        task_tab.rowconfigure(0, weight=1)

        ttk.Label(run_tab, text="Agent:").grid(row=0, column=0, sticky="w")
        self.agent_box = ttk.Combobox(run_tab, textvariable=self.agent_var, state="readonly", width=34)
        self.agent_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(run_tab, text="Prompt:").grid(row=1, column=0, sticky="nw", pady=(10, 0))
        self.prompt = tk.Text(run_tab, height=7, wrap="word")
        self.prompt.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(10, 0))
        self.prompt_yscroll = ttk.Scrollbar(run_tab, orient="vertical", command=self.prompt.yview)
        self.prompt_yscroll.grid(row=1, column=2, sticky="ns", pady=(10, 0))
        self.prompt.configure(yscrollcommand=self.prompt_yscroll.set)
        button_row = ttk.Frame(run_tab)
        button_row.grid(row=2, column=1, columnspan=2, sticky="e", pady=(10, 0))
        self.run_button = ttk.Button(button_row, text="Run agent", command=self.start_run)
        self.run_button.pack(side="right")
        self.run_cancel_button = ttk.Button(button_row, text="Cancel", command=self.cancel, state="disabled")
        self.run_cancel_button.pack(side="right", padx=(0, 8))

        ttk.Label(task_tab, text="Task description:").grid(row=0, column=0, sticky="nw")
        self.description = tk.Text(task_tab, height=5, wrap="word")
        self.description.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.description_yscroll = ttk.Scrollbar(task_tab, orient="vertical", command=self.description.yview)
        self.description_yscroll.grid(row=0, column=2, sticky="ns")
        self.description.configure(yscrollcommand=self.description_yscroll.set)
        task_button_row = ttk.Frame(task_tab)
        task_button_row.grid(row=1, column=1, columnspan=2, sticky="e", pady=(10, 0))
        self.task_button = ttk.Button(task_button_row, text="Run task workflow", command=self.start_task)
        self.task_button.pack(side="right")
        self.task_cancel_button = ttk.Button(task_button_row, text="Cancel", command=self.cancel, state="disabled")
        self.task_cancel_button.pack(side="right", padx=(0, 8))
        history_tab = ttk.Frame(notebook, padding=10)
        logs_tab = ttk.Frame(notebook, padding=10)
        notebook.add(history_tab, text="History")
        notebook.add(logs_tab, text="Logs")
        history_tab.columnconfigure(0, weight=1)
        history_tab.rowconfigure(2, weight=1)
        history_controls = ttk.Frame(history_tab)
        history_controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(history_controls, text="Status:").pack(side="left")
        self.history_box = ttk.Combobox(history_controls, textvariable=self.history_filter, state="readonly",
                                        width=12, values=("all", "pending", "running", "passed", "failed", "blocked"))
        self.history_box.pack(side="left", padx=(6, 12))
        self.history_box.bind("<<ComboboxSelected>>", lambda _event: self.reset_history())
        self.history_refresh_button = ttk.Button(history_controls, text="Refresh", command=self.refresh_history)
        self.history_refresh_button.pack(side="left")
        self.history_load_button = ttk.Button(history_controls, text="Load selected", command=self.load_selected_workflow)
        self.history_load_button.pack(side="left", padx=(8, 0))
        self.history_prev_button = ttk.Button(history_controls, text="Previous", command=self.history_previous)
        self.history_prev_button.pack(side="right")
        self.history_next_button = ttk.Button(history_controls, text="Next", command=self.history_next)
        self.history_next_button.pack(side="right", padx=(0, 8))
        self.history_nav_var = tk.StringVar(value="No workflows loaded")
        ttk.Label(history_tab, textvariable=self.history_nav_var).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.history_tree = ttk.Treeview(history_tab, columns=("id", "status", "tasks", "updated", "description"),
                                         show="headings", height=5)
        for column, title, width in (("id", "Workflow", 90), ("status", "Status", 90),
                                    ("tasks", "Tasks", 70), ("updated", "Updated", 170),
                                    ("description", "Description", 430)):
            self.history_tree.heading(column, text=title)
            self.history_tree.column(column, width=width, stretch=True)
        self.history_tree.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.history_vscroll = ttk.Scrollbar(history_tab, orient="vertical", command=self.history_tree.yview)
        self.history_vscroll.grid(row=2, column=1, sticky="ns", pady=(6, 0))
        self.history_hscroll = ttk.Scrollbar(history_tab, orient="horizontal", command=self.history_tree.xview)
        self.history_hscroll.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.history_tree.configure(yscrollcommand=self.history_vscroll.set,
                                    xscrollcommand=self.history_hscroll.set)
        logs_tab.columnconfigure(0, weight=1)
        logs_tab.rowconfigure(1, weight=1)
        logs_controls = ttk.Frame(logs_tab)
        logs_controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(logs_controls, text="Task filter:").pack(side="left")
        ttk.Entry(logs_controls, textvariable=self.log_task, width=30).pack(side="left", padx=(6, 12))
        self.logs_refresh_button = ttk.Button(logs_controls, text="Refresh", command=self.refresh_logs)
        self.logs_refresh_button.pack(side="left")
        self.log_view_button = ttk.Button(logs_controls, text="View selected", command=self.view_selected_log)
        self.log_view_button.pack(side="left", padx=(8, 0))
        self.log_copy_button = ttk.Button(logs_controls, text="Copy path", command=self.copy_selected_log_path)
        self.log_copy_button.pack(side="left", padx=(8, 0))
        self.log_folder_button = ttk.Button(logs_controls, text="Open folder", command=self.open_selected_log_folder)
        self.log_folder_button.pack(side="left", padx=(8, 0))
        self.log_list = tk.Listbox(logs_tab, height=8)
        self.log_list.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        logs_scroll = ttk.Scrollbar(logs_tab, orient="vertical", command=self.log_list.yview)
        logs_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.logs_hscroll = ttk.Scrollbar(logs_tab, orient="horizontal", command=self.log_list.xview)
        self.logs_hscroll.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.log_list.configure(yscrollcommand=logs_scroll.set, xscrollcommand=self.logs_hscroll.set)
        artifacts_tab = ttk.Frame(notebook, padding=10)
        notebook.add(artifacts_tab, text="Artifacts")
        artifacts_tab.columnconfigure(0, weight=1)
        artifacts_tab.rowconfigure(1, weight=1)
        artifacts_controls = ttk.Frame(artifacts_tab)
        artifacts_controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(artifacts_controls, text="Task filter:").pack(side="left")
        ttk.Entry(artifacts_controls, textvariable=self.artifact_task, width=30).pack(side="left", padx=(6, 12))
        self.artifacts_refresh_button = ttk.Button(artifacts_controls, text="Refresh", command=self.refresh_artifacts)
        self.artifacts_refresh_button.pack(side="left")
        self.artifact_view_button = ttk.Button(artifacts_controls, text="View selected", command=self.view_selected_artifact)
        self.artifact_view_button.pack(side="left", padx=(8, 0))
        self.artifact_list = tk.Listbox(artifacts_tab, height=8)
        self.artifact_list.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        artifacts_scroll = ttk.Scrollbar(artifacts_tab, orient="vertical", command=self.artifact_list.yview)
        artifacts_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.artifacts_hscroll = ttk.Scrollbar(artifacts_tab, orient="horizontal",
                                               command=self.artifact_list.xview)
        self.artifacts_hscroll.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.artifact_list.configure(yscrollcommand=artifacts_scroll.set,
                                     xscrollcommand=self.artifacts_hscroll.set)
        worktrees_tab = ttk.Frame(notebook, padding=10)
        notebook.add(worktrees_tab, text="Worktrees")
        worktrees_tab.columnconfigure(0, weight=1)
        worktrees_tab.rowconfigure(1, weight=1)
        worktrees_controls = ttk.Frame(worktrees_tab)
        worktrees_controls.grid(row=0, column=0, sticky="ew")
        self.worktrees_refresh_button = ttk.Button(worktrees_controls, text="Refresh",
                                                   command=self.refresh_worktrees)
        self.worktrees_refresh_button.pack(side="left")
        self.worktree_inspect_button = ttk.Button(worktrees_controls, text="Inspect selected",
                                                  command=self.inspect_selected_worktree)
        self.worktree_inspect_button.pack(side="left", padx=(8, 0))
        self.worktree_cleanup_button = ttk.Button(worktrees_controls, text="Clean up selected",
                                                  command=self.cleanup_selected_worktree)
        self.worktree_cleanup_button.pack(side="left", padx=(8, 0))
        self.worktree_retry_button = ttk.Button(worktrees_controls, text="Retry merge",
                                                command=self.retry_selected_merge)
        self.worktree_retry_button.pack(side="left", padx=(8, 0))
        self.worktree_folder_button = ttk.Button(worktrees_controls, text="Open folder",
                                                 command=self.open_selected_worktree_folder)
        self.worktree_folder_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(worktrees_controls, text="Delete unmerged branch",
                        variable=self.delete_unmerged_branch).pack(side="right")
        self.worktree_tree = ttk.Treeview(worktrees_tab, columns=("path", "branch", "head", "state"),
                                          show="headings", height=5)
        for column, title, width in (("path", "Path", 420), ("branch", "Branch", 220),
                                    ("head", "Commit", 90), ("state", "Working tree", 160)):
            self.worktree_tree.heading(column, text=title)
            self.worktree_tree.column(column, width=width, stretch=True)
        self.worktree_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.worktree_vscroll = ttk.Scrollbar(worktrees_tab, orient="vertical", command=self.worktree_tree.yview)
        self.worktree_vscroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.worktree_hscroll = ttk.Scrollbar(worktrees_tab, orient="horizontal",
                                              command=self.worktree_tree.xview)
        self.worktree_hscroll.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.worktree_tree.configure(yscrollcommand=self.worktree_vscroll.set,
                                     xscrollcommand=self.worktree_hscroll.set)

        status_frame = ttk.LabelFrame(main, text="Workflow status", padding=8)
        status_frame.grid(row=5, column=0, columnspan=4, sticky="nsew")
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(1, weight=1)
        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.task_tree = ttk.Treeview(status_frame, columns=("id", "role", "status", "agent", "description"),
                                      show="headings", height=5)
        for column, title, width in (("id", "Task", 90), ("role", "Role", 120), ("status", "Status", 90),
                                    ("agent", "Agent", 120), ("description", "Description", 520)):
            self.task_tree.heading(column, text=title)
            self.task_tree.column(column, width=width, stretch=True)
        self.task_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.task_vscroll = ttk.Scrollbar(status_frame, orient="vertical", command=self.task_tree.yview)
        self.task_vscroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.task_hscroll = ttk.Scrollbar(status_frame, orient="horizontal", command=self.task_tree.xview)
        self.task_hscroll.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.task_tree.configure(yscrollcommand=self.task_vscroll.set,
                                 xscrollcommand=self.task_hscroll.set)
        self.task_details_button = ttk.Button(status_frame, text="Show selected task details",
                                             command=self.show_selected_task)
        self.task_details_button.grid(row=3, column=0, columnspan=2, sticky="e", pady=(6, 0))

        output_frame = ttk.LabelFrame(main, text="Output", padding=8)
        output_frame.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output = tk.Text(output_frame, height=6, wrap="word", state="disabled")
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scrollbar.set)
        self.clear_button = ttk.Button(output_frame, text="Clear", command=self.clear_output)
        self.clear_button.grid(row=1, column=0, sticky="e", pady=(6, 0))

    def choose_repository(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Select target repository")
        if selected:
            self.repository.set(selected)
            self.refresh_all()

    def choose_config(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="Select AgentOps config",
                                              filetypes=(("YAML/JSON", "*.yaml *.yml *.json"),
                                                         ("All files", "*.*")))
        if selected:
            self.config_path.set(selected)
            self.refresh_agents()

    def _effective_controller(self) -> GuiController:
        if self._operation_controller is not None:
            return self._operation_controller
        path = self.config_path.get().strip()
        if path:
            if self._config_controller is None or self._config_controller_path != path:
                self._config_controller = AgentOpsController(config_path=Path(path))
                self._config_controller_path = path
            return self._config_controller
        return self.controller

    def refresh_all(self) -> None:
        self.refresh_agents()
        self.refresh_status()

    def refresh_agents(self) -> None:
        try:
            agents = self._effective_controller().detect_agents()
            names = sorted(str(name) for name in agents)
            self.agent_box["values"] = names
            if names and self.agent_var.get() not in names:
                self.agent_var.set(names[0])
            if not self._running:
                self.status_var.set(f"Detected {len(names)} configured agent(s)")
        except Exception as error:
            self.agent_box["values"] = ()
            if not self._running:
                self.status_var.set("Configuration error")
            self.show_error(error, "Unable to load AgentOps configuration")

    def refresh_status(self) -> None:
        self._refresh_status(notify=True)

    def _refresh_status(self, notify: bool) -> None:
        try:
            workflow = self._effective_controller().latest_workflow(self.repository.get().strip())
            if workflow is None:
                self._update_tasks(())
                if not self._running:
                    self.status_var.set("Ready - no persisted workflow")
                self._last_workflow_id = None
                return
            status = _status_text(workflow.get("status"))
            tasks = workflow.get("tasks", ())
            task_list = list(tasks) if isinstance(tasks, (list, tuple)) else []
            self._update_tasks(task_list)
            if self._last_workflow_id != str(workflow.get("id", "")):
                self._last_workflow_id = str(workflow.get("id", ""))
                self.append_output(f"Workflow {workflow.get('id')}  {status}")
                description = str(workflow.get("description", ""))
                if description:
                    self.append_output(description)
            if not self._running:
                self.status_var.set(f"Latest workflow: {status} ({len(task_list)} tasks)")
        except Exception as error:
            if not self._running:
                self.status_var.set("Status unavailable")
            self.append_output(f"ERROR: {error}")
            if notify:
                self.show_error(error, "Unable to read workflow status")

    @staticmethod
    def _task_signature(tasks: list[object]) -> tuple[tuple[str, ...], ...]:
        # Cheap change key so the 1s status poll can skip tree rebuilds.
        # Covers everything the table shows plus progress signals.
        return tuple(tuple(_task_value(task, name) for name in (
            "id", "role", "status", "assigned_agent", "attempts",
            "verified", "description",
        )) for task in tasks)

    def _update_tasks(self, tasks: list[object], force: bool = False) -> None:
        task_list = list(tasks)
        signature = self._task_signature(task_list)
        if not force and signature == getattr(self, "_task_signature_cache", None):
            return
        self._task_signature_cache = signature
        self._current_tasks = task_list
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for index, task in enumerate(self._current_tasks):
            task_id = _task_value(task, "id", f"task-{index + 1}")
            self.task_tree.insert("", "end", iid=f"task-{index}",
                                  values=(task_id, _task_value(task, "role"),
                                          _status_text(_task_value(task, "status")),
                                          _task_value(task, "assigned_agent"),
                                          _short_text(_task_value(task, "description"))))

    def show_selected_task(self) -> None:
        selection = self.task_tree.selection()
        if not selection:
            messagebox.showwarning("No task selected", "Select a task in the workflow status table.",
                                    parent=self.root)
            return
        try:
            index = int(str(selection[0]).rsplit("-", 1)[-1])
            task = self._current_tasks[index]
        except (ValueError, IndexError):
            messagebox.showwarning("Invalid selection", "Select a task in the workflow status table.",
                                    parent=self.root)
            return
        self.append_output(f"Task {_task_value(task, 'id')}  {_status_text(_task_value(task, 'status'))}")
        self.append_output(f"Role: {_task_value(task, 'role')}  Agent: {_task_value(task, 'assigned_agent')}")
        self.append_output(f"Attempts: {_task_value(task, 'attempts')}  Dependencies: {_task_value(task, 'dependencies')}")
        description = _task_value(task, 'description')
        if description:
            self.append_output(f"Request: {description}")
        result = _task_value(task, 'result')
        if result:
            self.append_output("Result:")
            self.append_output(result)
        task_id = _task_value(task, 'id')
        list_runs = getattr(self._effective_controller(), "list_agent_runs", None)
        if task_id and callable(list_runs):
            try:
                runs = list_runs(self.repository.get().strip(), task_id=task_id)
            except Exception as error:
                self.append_output(f"Agent runs unavailable: {error}")
            else:
                runs = list(runs) if isinstance(runs, (list, tuple)) else []
                if runs:
                    self.append_output("Agent runs:")
                    for run in runs:
                        if isinstance(run, dict):
                            duration = run.get("duration_seconds")
                            duration_text = f"{float(duration):.1f}s" if duration is not None else "-"
                            self.append_output(
                                f"- {run.get('status')} {run.get('agent') or '<no-agent>'} "
                                f"attempt {run.get('attempt')} exit {run.get('exit_code')} "
                                f"duration {duration_text}"
                            )
                            if run.get("log_path"):
                                self.append_output(f"  Log: {run.get('log_path')}")
                        else:
                            self.append_output(f"- {run}")
        list_verifications = getattr(self._effective_controller(), "list_verification_runs", None)
        if task_id and callable(list_verifications):
            try:
                verifications = list_verifications(self.repository.get().strip(), task_id=task_id)
            except Exception as error:
                self.append_output(f"Verification unavailable: {error}")
            else:
                verifications = list(verifications) if isinstance(verifications, (list, tuple)) else []
                if verifications:
                    self.append_output("Verification:")
                    for verification in verifications:
                        if isinstance(verification, dict):
                            self.append_output(
                                f"- {verification.get('overall_status') or verification.get('status')} "
                                f"{verification.get('profile_name')} "
                                f"passed {verification.get('passed_checks')}/{verification.get('total_checks')}"
                            )
                        else:
                            self.append_output(f"- {verification}")
        list_failures = getattr(self._effective_controller(), "list_failures", None)
        if task_id and callable(list_failures):
            try:
                failures = list_failures(self.repository.get().strip(), task_id=task_id)
            except Exception as error:
                self.append_output(f"Failures unavailable: {error}")
            else:
                failures = list(failures) if isinstance(failures, (list, tuple)) else []
                if failures:
                    self.append_output("Failures:")
                    for failure in failures:
                        if isinstance(failure, dict):
                            self.append_output(
                                f"- {failure.get('category')} [{failure.get('severity')}] "
                                f"action={failure.get('recommended_action')} "
                                f"retryable={failure.get('retryable')} repairable={failure.get('repairable')}"
                            )
                        else:
                            self.append_output(f"- {failure}")

    def reset_history(self) -> None:
        self._history_offset = 0
        self.refresh_history()

    def refresh_history(self) -> None:
        try:
            status = self.history_filter.get().strip() or "all"
            controller = self._effective_controller()
            repository = self.repository.get().strip()
            page = controller.list_workflows(
                repository, limit=self._history_limit, offset=self._history_offset,
                status=None if status == "all" else status)
            total = int(page.get("total", 0))
            if total and self._history_offset >= total:
                self._history_offset = max(0, (total - 1) // self._history_limit * self._history_limit)
                page = controller.list_workflows(
                    repository, limit=self._history_limit, offset=self._history_offset,
                    status=None if status == "all" else status)
                total = int(page.get("total", 0))
            rows = page.get("workflows", [])
            self._history_total = total
            self._history_rows = list(rows) if isinstance(rows, list) else []
            for item in self.history_tree.get_children():
                self.history_tree.delete(item)
            for index, row in enumerate(self._history_rows):
                self.history_tree.insert("", "end", iid=f"history-{index}", values=(
                    str(row.get("id", "")), _status_text(row.get("status")), str(row.get("task_count", "")),
                    str(row.get("updated_at", "")), _short_text(str(row.get("description", "")))))
            if total:
                first = self._history_offset + 1
                last = min(self._history_offset + len(self._history_rows), total)
                self.history_nav_var.set(f"Showing {first}-{last} of {total} workflows")
            else:
                self.history_nav_var.set("No workflows match this filter")
        except Exception as error:
            self.history_nav_var.set("History unavailable")
            self.show_error(error, "Unable to read workflow history")

    def history_previous(self) -> None:
        self._history_offset = max(0, self._history_offset - self._history_limit)
        self.refresh_history()

    def history_next(self) -> None:
        if self._history_offset + self._history_limit < self._history_total:
            self._history_offset += self._history_limit
            self.refresh_history()

    def load_selected_workflow(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showwarning("No workflow selected", "Select a workflow in the history table.",
                                    parent=self.root)
            return
        try:
            index = int(str(selection[0]).rsplit("-", 1)[-1])
            workflow_id = str(self._history_rows[index].get("id", ""))
        except (ValueError, IndexError, AttributeError):
            messagebox.showwarning("Invalid selection", "Select a workflow in the history table.",
                                    parent=self.root)
            return
        try:
            workflow = self._effective_controller().get_workflow(self.repository.get().strip(), workflow_id)
            if workflow is None:
                messagebox.showwarning("Workflow unavailable", "The selected workflow is no longer present.",
                                        parent=self.root)
                return
            tasks = workflow.get("tasks", [])
            task_list = list(tasks) if isinstance(tasks, (list, tuple)) else []
            self._update_tasks(task_list)
            self._last_workflow_id = str(workflow.get("id", ""))
            if not self._running:
                self.status_var.set(f"Workflow: {_status_text(workflow.get('status'))} ({len(task_list)} tasks)")
            self.append_output(f"Workflow {workflow.get('id')}  {_status_text(workflow.get('status'))}")
            if workflow.get("description"):
                self.append_output(str(workflow["description"]))
        except Exception as error:
            self.show_error(error, "Unable to load workflow")

    def refresh_logs(self) -> None:
        try:
            task_filter = self.log_task.get().strip() or None
            self._log_entries = self._effective_controller().list_logs(self.repository.get().strip(), task_filter)
            self.log_list.delete(0, "end")
            for entry in self._log_entries:
                size = int(entry.get("size", 0))
                self.log_list.insert("end", f"{entry.get('name', '')} ({size:,} bytes)")
            self.append_output(f"Found {len(self._log_entries)} log file(s).")
        except Exception as error:
            self.show_error(error, "Unable to list logs")

    def _selected_log(self) -> dict[str, object] | None:
        selection = self.log_list.curselection()
        if not selection:
            messagebox.showwarning("No log selected", "Select a log file first.", parent=self.root)
            return None
        try:
            return self._log_entries[selection[0]]
        except IndexError:
            messagebox.showwarning("Invalid selection", "Select a log file first.", parent=self.root)
            return None

    def view_selected_log(self) -> None:
        entry = self._selected_log()
        if entry is None:
            return
        try:
            data = self._effective_controller().read_log(self.repository.get().strip(), str(entry.get("name", "")))
            self.append_output(f"Log: {data.get('name', '')}")
            if data.get("truncated"):
                self.append_output("Showing the most recent part of this log; older content was truncated.")
            self.append_output(str(data.get("text", "")).rstrip() or "(empty log)")
        except Exception as error:
            self.show_error(error, "Unable to read log")

    def refresh_artifacts(self) -> None:
        try:
            list_artifacts = getattr(self._effective_controller(), "list_artifacts", None)
            if not callable(list_artifacts):
                self.append_output("Artifacts unavailable in this controller.")
                return
            task_filter = self.artifact_task.get().strip() or None
            entries = list_artifacts(self.repository.get().strip(), task_id=task_filter)
            self._artifact_entries = list(entries) if isinstance(entries, (list, tuple)) else []
            self.artifact_list.delete(0, "end")
            for entry in self._artifact_entries:
                size = entry.get("size_bytes", 0)
                try:
                    size_text = f"{int(size):,} bytes"
                except (TypeError, ValueError):
                    size_text = "-"
                self.artifact_list.insert("end", f"{entry.get('kind', '')} {entry.get('name', '')} ({size_text})")
            self.append_output(f"Found {len(self._artifact_entries)} artifact(s).")
        except Exception as error:
            self.show_error(error, "Unable to list artifacts")

    def view_selected_artifact(self) -> None:
        selection = self.artifact_list.curselection()
        if not selection:
            messagebox.showwarning("No artifact selected", "Select an artifact first.", parent=self.root)
            return
        try:
            entry = self._artifact_entries[selection[0]]
        except IndexError:
            messagebox.showwarning("Invalid selection", "Select an artifact first.", parent=self.root)
            return
        try:
            read_artifact = getattr(self._effective_controller(), "read_artifact", None)
            if not callable(read_artifact):
                self.append_output("Artifact reading unavailable in this controller.")
                return
            data = read_artifact(self.repository.get().strip(), str(entry.get("id", "")))
            artifact = data.get("artifact") if isinstance(data, dict) else None
            name = artifact.get("name", "") if isinstance(artifact, dict) else ""
            self.append_output(f"Artifact: {name}")
            text = data.get("text", "") if isinstance(data, dict) else ""
            self.append_output(str(text).rstrip() or "(empty artifact)")
        except Exception as error:
            self.show_error(error, "Unable to read artifact")

    def copy_selected_log_path(self) -> None:
        entry = self._selected_log()
        if entry is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(entry.get("path", "")))
        self.append_output(f"Copied log path: {entry.get('path', '')}")

    def open_selected_log_folder(self) -> None:
        entry = self._selected_log()
        if entry is None:
            return
        self._open_path(Path(str(entry.get("path", ""))).parent)

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform == "win32" and hasattr(os, "startfile"):
                os.startfile(str(path))  # noqa: S606 - local GUI file navigation only
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as error:
            self.show_error(error, "Unable to open folder")

    def refresh_worktrees(self) -> None:
        try:
            self._worktree_entries = self._effective_controller().list_worktrees(self.repository.get().strip())
            for item in self.worktree_tree.get_children():
                self.worktree_tree.delete(item)
            for index, entry in enumerate(self._worktree_entries):
                status = str(entry.get("status", "")).strip()
                state = "clean" if not status else ("unavailable" if status == "(status unavailable)" else "dirty")
                scope = "" if entry.get("managed") else " (unmanaged)"
                self.worktree_tree.insert("", "end", iid=f"worktree-{index}", values=(
                    str(entry.get("path", "")), str(entry.get("branch", "")) + scope,
                    _short_text(str(entry.get("head", "")), 12), state))
            self.append_output(f"Found {len(self._worktree_entries)} Git worktree(s).")
        except Exception as error:
            self.show_error(error, "Unable to list worktrees")

    def _selected_worktree(self) -> dict[str, object] | None:
        selection = self.worktree_tree.selection()
        if not selection:
            messagebox.showwarning("No worktree selected", "Select a worktree first.", parent=self.root)
            return None
        try:
            return self._worktree_entries[int(str(selection[0]).rsplit("-", 1)[-1])]
        except (ValueError, IndexError):
            messagebox.showwarning("Invalid selection", "Select a worktree first.", parent=self.root)
            return None

    def inspect_selected_worktree(self) -> None:
        entry = self._selected_worktree()
        if entry is None:
            return
        try:
            info = self._effective_controller().inspect_worktree(self.repository.get().strip(),
                                                                 str(entry.get("path", "")))
            self.append_output(f"Worktree: {info.get('path')}")
            self.append_output(f"Branch: {info.get('branch')}  Commit: {info.get('head')}")
            status = str(info.get("status", "")).strip() or "clean"
            self.append_output(f"Working tree: {status}")
            diff_stat = str(info.get("diff_stat", "")).strip()
            if diff_stat:
                self.append_output("Changed files:")
                self.append_output(diff_stat)
        except Exception as error:
            self.show_error(error, "Unable to inspect worktree")

    def cleanup_selected_worktree(self) -> None:
        entry = self._selected_worktree()
        if entry is None:
            return
        path = str(entry.get("path", ""))
        if self.delete_unmerged_branch.get():
            confirmed = messagebox.askyesno(
                "Delete unmerged branch", f"Remove this worktree and force-delete its unmerged branch?\n\n{path}",
                parent=self.root)
            if not confirmed:
                return
        else:
            confirmed = messagebox.askyesno(
                "Clean up worktree", f"Remove this clean worktree? An unmerged branch will be preserved.\n\n{path}",
                parent=self.root)
            if not confirmed:
                return
        try:
            result = self._effective_controller().cleanup_worktree(
                self.repository.get().strip(), path, self.delete_unmerged_branch.get())
            if result.get("deleted_branch"):
                self.append_output(f"Removed worktree and deleted branch {result.get('branch')}.")
            else:
                self.append_output(f"Removed worktree; preserved branch {result.get('branch')}.")
                if result.get("detail"):
                    self.append_output(str(result["detail"]))
            self.refresh_worktrees()
        except Exception as error:
            self.show_error(error, "Unable to clean up worktree")

    def retry_selected_merge(self) -> None:
        entry = self._selected_worktree()
        if entry is None:
            return
        path = str(entry.get("path", ""))
        confirmed = messagebox.askyesno(
            "Retry merge", "Merge this managed worktree branch into the current base branch now?\n\n" + path,
            parent=self.root)
        if not confirmed:
            return
        try:
            result = self._effective_controller().retry_merge(self.repository.get().strip(), path)
            self.append_output(f"Merged {result.get('branch')} into {result.get('base_branch')}.")
            self.refresh_worktrees()
            self.refresh_status()
        except Exception as error:
            self.show_error(error, "Unable to retry merge")

    def open_selected_worktree_folder(self) -> None:
        entry = self._selected_worktree()
        if entry is None:
            return
        self._open_path(Path(str(entry.get("path", ""))))

    def start_run(self) -> None:
        if self._running:
            messagebox.showwarning("Operation running", "Wait for the current operation to finish.",
                                   parent=self.root)
            return
        agent = self.agent_var.get().strip()
        prompt = self.prompt.get("1.0", "end").strip()
        if not agent or not prompt:
            messagebox.showwarning("Missing input", "Select an agent and enter a prompt.", parent=self.root)
            return
        repository = self.repository.get().strip()
        if not repository:
            messagebox.showwarning("Missing input", "Select a target repository.", parent=self.root)
            return
        self._start_operation(f"Running {agent} in {repository}...",
                              lambda controller: controller.run_agent(agent, prompt, repository, self._emit),
                              "Unable to start agent")

    def start_task(self) -> None:
        if self._running:
            messagebox.showwarning("Operation running", "Wait for the current operation to finish.",
                                   parent=self.root)
            return
        description = self.description.get("1.0", "end").strip()
        if not description:
            messagebox.showwarning("Missing input", "Enter a task description.", parent=self.root)
            return
        repository = self.repository.get().strip()
        if not repository:
            messagebox.showwarning("Missing input", "Select a target repository.", parent=self.root)
            return
        self._start_operation(f"Starting task workflow in {repository}...",
                              lambda controller: controller.run_task(description, repository, self._emit),
                              "Unable to start task workflow")

    def _start_operation(self, message: str, start: Callable[[GuiController], None], error_title: str) -> None:
        try:
            controller = self._effective_controller()
            self._operation_seq += 1
            self._operation_id = self._operation_seq
            self._operation_controller = controller
            self._set_running(True)
            self.append_output(message)
            start(controller)
        except Exception as error:
            self._operation_controller = None
            self._set_running(False)
            self.show_error(error, error_title)

    def _emit(self, event: dict[str, object]) -> None:
        # Never touch Tk widgets from a worker thread: queue the event and let
        # the Tk thread validate it. A destroyed root raises here and is ignored.
        event["operation_id"] = self._operation_id
        try:
            self.root.after(0, self._handle_event, event)
        except (tk.TclError, RuntimeError):
            pass

    def _handle_event(self, event: dict[str, object]) -> None:
        if not self._is_alive():
            return
        if event.get("operation_id") != self._operation_id:
            return
        kind = str(event.get("kind", ""))
        if kind == "thread-finished":
            self._finish_operation()
            return
        if kind == "workflow-started":
            self.append_output(f"Worktree: {event.get('worktree', '')}")
            self._refresh_status(notify=False)
            self._schedule_status_poll()
        elif kind == "workflow-result":
            result = event.get("result")
            summary = getattr(result, "summary", result)
            self.append_output(f"Workflow finished: {summary}")
            if event.get("worktree"):
                self.append_output(f"Worktree: {event.get('worktree')}")
            if event.get("merged"):
                self.append_output("Worktree changes were merged.")
            self._finish_operation()
            self._refresh_status(notify=False)
        elif kind == "result":
            status = "passed" if event.get("succeeded") else "failed"
            duration = float(event.get("duration_seconds", 0.0) or 0.0)
            self.append_output(f"{event.get('agent')} {status} ({duration:.1f}s)")
            for key in ("stdout", "stderr"):
                if event.get(key):
                    self.append_output(str(event[key]).rstrip())
            if event.get("run_id"):
                self.append_output(f"Run: {event.get('run_id')} ({event.get('run_status', 'unknown')})")
            if event.get("log_path"):
                self.append_output(f"Log: {event['log_path']}")
            self._finish_operation()
        elif kind == "cancelled":
            self.append_output("Operation cancelled. Partial state was preserved for inspection.")
            self._finish_operation()
            self._refresh_status(notify=False)
        elif kind == "conflict":
            self.append_output(f"Merge conflict preserved at {event.get('worktree', '')}.")
            self.append_output(f"A conflict-resolution task was added to workflow {event.get('workflow_id')}.")
            self._finish_operation()
            self._refresh_status(notify=False)
            self.show_error(event.get("error"), "Git merge conflict")
        elif kind == "error":
            self.append_output("Operation failed before completion; partial state was preserved.")
            self._finish_operation()
            self._refresh_status(notify=False)
            self.show_error(event.get("error"), "AgentOps operation failed")

    def _finish_operation(self) -> None:
        self._cancel_status_poll()
        self._operation_controller = None
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        cancel_state = "normal" if running else "disabled"
        for widget in (self.repository_entry, self.config_entry, self.browse_repository_button,
                       self.browse_config_button, self.refresh_button, self.detect_button,
                       self.run_button, self.task_button):
            widget.configure(state=state)
        for widget in (self.run_cancel_button, self.task_cancel_button):
            widget.configure(state=cancel_state)
        if running:
            self.status_var.set("Running...")
        self._schedule_status_poll() if running else self._cancel_status_poll()

    def cancel(self) -> None:
        controller = self._operation_controller
        if controller is None:
            return
        try:
            controller.cancel()
            self.append_output("Cancellation requested.")
        except Exception as error:
            self.show_error(error, "Unable to cancel operation")

    def _schedule_status_poll(self) -> None:
        self._cancel_status_poll()
        if self._running and self._is_alive():
            self._status_poll_id = self.root.after(1000, self._poll_status)

    def _cancel_status_poll(self) -> None:
        if self._status_poll_id is not None:
            with suppress(tk.TclError):
                self.root.after_cancel(self._status_poll_id)
            self._status_poll_id = None

    def _poll_status(self) -> None:
        self._status_poll_id = None
        if not self._running or not self._is_alive():
            return
        self._refresh_status(notify=False)
        self._schedule_status_poll()

    def append_output(self, text: str) -> None:
        if not self._is_alive():
            return
        self.output.configure(state="normal")
        self.output.insert("end", text if text.endswith("\n") else text + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def clear_output(self) -> None:
        if not self._is_alive():
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def show_error(self, error: object, title: str) -> None:
        message = str(error)
        self.append_output(f"ERROR: {message}")
        if self._is_alive():
            with suppress(tk.TclError):
                messagebox.showerror(title, message, parent=self.root)

    def _is_alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def close(self) -> None:
        self._cancel_status_poll()
        controller, self._operation_controller = self._operation_controller, None
        if controller is not None:
            with suppress(Exception):
                controller.cancel()
        with suppress(tk.TclError):
            self.root.destroy()


def main(config_path: str | Path | None = None) -> None:
    path = Path(config_path) if config_path else None
    AgentOpsApp(controller=AgentOpsController(config_path=path)).root.mainloop()


if __name__ == "__main__":
    main()
