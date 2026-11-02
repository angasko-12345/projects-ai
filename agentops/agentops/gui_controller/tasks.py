"""AgentOpsController: task listing and task management."""

from __future__ import annotations

from pathlib import Path

from ..tasks import Task
from ..workflow import STANDARD_TASK_ROLES
from .. import gui_controller as _ns


class TasksMixin:
    """Task listing and task management."""

    def list_tasks(
        self,
        directory: str | Path,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """All tasks (optionally one workflow/status), newest first.

        Read-only facade for the Tasks surface; ``state.list_tasks`` stores
        rows oldest-first, so the payload is reversed for display.
        """
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 0 or offset < 0:
            raise ValueError("Task list limit and offset must be non-negative integers.")
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            tasks = state.list_tasks(workflow_id)
            if status is not None:
                tasks = [
                    task
                    for task in tasks
                    if (task.status.value if hasattr(task.status, "value") else str(task.status))
                    == status
                ]
            tasks.reverse()
            return [_ns.serialize_task(task) for task in tasks[offset : offset + limit]]
        finally:
            state.close()

    # -- task management -------------------------------------------------
    #: Roles the Tasks surface allows when recategorizing. The engine builds
    #: the standard four plus ad-hoc debugging tasks, so anything outside
    #: this set would render as a custom DAG node the pipeline never owns.
    TASK_ROLES = STANDARD_TASK_ROLES + ("debugging",)

    @staticmethod
    def _task_status(task: Task) -> str:
        status = task.status
        return str(getattr(status, "value", status))

    def rename_task(
        self, directory: str | Path, task_id: str, description: str
    ) -> dict[str, object]:
        """Replace a task's description (its display name everywhere)."""
        text = str(description or "").strip()
        if not text:
            raise ValueError("A task description cannot be empty.")
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            if self._task_status(task) == "running":
                raise ValueError("A running task cannot be renamed.")
            task.description = text
            state.update_task(task)
            state.refresh_workflow_status(task.workflow_id)
            return _ns.serialize_task(task)
        finally:
            state.close()

    def recategorize_task(
        self, directory: str | Path, task_id: str, role: str
    ) -> dict[str, object]:
        """Move a task to another role (its category in the pipeline)."""
        wanted = str(role or "").strip().lower()
        if wanted not in self.TASK_ROLES:
            raise ValueError(f"Unknown role {role!r}; choose one of {', '.join(self.TASK_ROLES)}.")
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            status = self._task_status(task)
            if status not in ("pending", "blocked", "failed"):
                raise ValueError(
                    f"Only pending, blocked, or failed tasks can change role (task is {status})."
                )
            task.role = wanted
            state.update_task(task)
            state.refresh_workflow_status(task.workflow_id)
            return _ns.serialize_task(task)
        finally:
            state.close()

    def delete_task(self, directory: str | Path, task_id: str) -> dict[str, object]:
        """Remove a task row; recorded runs keep their history text.

        Refuses live work (a running task) and the workflow's last task,
        so execution and the header status always have something to derive
        from.
        """
        root = self._operation_root(directory)
        state = _ns.StateStore(self._state_path(root))
        try:
            task = state.get_task(str(task_id))
            if self._task_status(task) == "running":
                raise ValueError("A running task cannot be deleted.")
            siblings = state.list_tasks(task.workflow_id)
            if len(siblings) <= 1:
                raise ValueError("A workflow keeps at least one task.")
            removed = state.delete_task(task.id)
            if not removed:
                raise KeyError(f"Unknown task: {task_id}")
            state.refresh_workflow_status(task.workflow_id)
            return {"id": task.id, "workflow_id": task.workflow_id, "description": task.description}
        finally:
            state.close()

    @staticmethod
    def _record_status(record: object) -> str:
        status = getattr(record, "status", "")
        return str(getattr(status, "value", status))
