"""Agent prompt construction and dependency handoff."""

from __future__ import annotations

from pathlib import Path

from ..failure import build_retry_prompt
from ..security import findings_for_prompt
from ..tasks import Task, TaskStatus
from .support import (
    HANDOFF_RESULT_CHARS,
    HANDOFF_TOTAL_CHARS,
)
from .verdicts import _requires_review_verdict
from .security import (
    _is_security_role,
    _local_security_scan,
    _role_scope_text,
)


class PromptMixin:
    """WorkflowEngine methods: agent prompt construction and dependency handoff."""

    def _dependency_handoff(self, task: Task) -> str:
        """Bounded prompt context from this task's completed dependencies.

        A dependent agent is only useful if it can see what the task it depends
        on produced: without this, `implementation` never reads the architecture
        plan and `review` never inspects the change. Deterministic by
        construction -- dependency order, fixed truncation, no LLM. Reads the
        already-redacted `task.result`; prompts are never persisted.
        """
        if not task.dependencies:
            return ""
        try:
            tasks = {item.id: item for item in self.state.list_tasks(task.workflow_id)}
        except Exception:
            return ""
        blocks: list[str] = []
        budget = HANDOFF_TOTAL_CHARS
        for dependency_id in task.dependencies:
            dependency = tasks.get(dependency_id)
            if dependency is None or dependency.id == task.id:
                continue
            # Only a PASSED dependency has a result worth passing on. The
            # workflow schedules a task once its dependencies pass, so this
            # covers a dependency reached through a retry.
            if dependency.status is not TaskStatus.PASSED:
                continue
            result = (dependency.result or "").strip()
            if not result:
                continue
            if len(result) > HANDOFF_RESULT_CHARS:
                result = result[:HANDOFF_RESULT_CHARS].rstrip() + "\n[truncated]"
            block = (
                f"Previous task:\nRole: {dependency.role}\n\n"
                f"Description:\n{dependency.description}\n\n"
                f"Result:\n{result}\n"
            )
            if len(block) > budget:
                break
            blocks.append(block)
            budget -= len(block)
        if not blocks:
            return ""
        return "Completed dependency work you can rely on:\n" + "\n".join(blocks)

    def _prompt_with_history(self, task: Task, working_directory: str | Path) -> str:
        base = self._prompt(task, working_directory)
        if task.attempts <= 1:
            return base
        try:
            failures = self.state.list_failures(task.workflow_id, task.id, limit=1)
        except Exception:
            return base
        if not failures:
            return base
        try:
            return build_retry_prompt(base, failures[0])
        except Exception:
            return base

    def _prompt(self, task: Task, working_directory: str | Path) -> str:
        # The fallback path in `_run_task_agent` carries the same dependency
        # context, so a prompt does not depend on whether history read worked.
        common = (
            f"You are the {task.role} agent for AgentOps task {task.id}.\n"
            f"Workspace: {Path(working_directory).resolve()}\n"
            f"Request: {task.description}\n"
            "Work only in this workspace. Do not read secrets or alter AgentOps configuration. "
        )
        if _is_security_role(task.role):
            try:
                findings, completed = _local_security_scan(working_directory)
                scan_report = (
                    findings_for_prompt(findings)
                    if completed
                    else "Local secret scan: unavailable (the scan did not "
                    "complete; no prescan findings either way)."
                )
            except Exception:
                scan_report = "Local secret scan: unavailable."
            base = common + (
                "Security-review the completed change. Inspect each item and "
                "report concrete file:line evidence for anything suspicious: "
                "(1) secrets or credentials in the diff; "
                "(2) authentication and permission changes; "
                "(3) dependency, manifest, and lockfile changes; "
                "(4) newly added shell commands or network calls; "
                "(5) files changed outside the task's declared scope. "
                f"{scan_report} "
                "Never quote a suspected secret value in your output -- name the file and pattern only. "
                "Summarize your findings first. Your FINAL output line "
                "MUST be one valid JSON object using this schema: "
                '{"schema_version": 1, "verdict": "approve", "blocking": [], '
                '"non_blocking": [], "confidence": 0.9}. '
                'Use verdict="reject" if any blocking issue remains or you cannot approve safely. '
                "Both finding fields must be arrays of strings; include every field. Approve only with "
                "an empty blocking array. Do not put any other JSON object after the verdict, and do not "
                "write anything after its final line."
            )
        elif _requires_review_verdict(task.role):
            base = common + (
                "Review the completed work and summarize your findings first. Your FINAL output line "
                "MUST be one valid JSON object using this schema: "
                '{"schema_version": 1, "verdict": "approve", "blocking": [], '
                '"non_blocking": [], "confidence": 0.9}. '
                'Use verdict="reject" if any blocking issue remains or you cannot approve safely. '
                "Both finding fields must be arrays of strings; include every field. Approve only with "
                "an empty blocking array. Do not put any other JSON object after the verdict, and do not "
                "write anything after its final line."
            )
        else:
            base = common + (
                "Summarize changes and tests in your final response. "
                "Your FINAL output line MUST be one valid JSON AgentResult "
                "object using exactly this schema: "
                '{"schema_version": 1, "status": "success|failure|partial", '
                '"summary": "...", "files_changed": [], "tests_run": 0, '
                '"tests_passed": 0, "tests_failed": 0}. '
                "Use status=failure when the work could not be completed, "
                "with a plain-language summary of what happened. "
                "Do not write anything after that final JSON line."
            )
        scope = _role_scope_text(task.role, working_directory)
        if scope:
            base = f"{base}\n\nRole scope:\n{scope}"
        handoff = self._dependency_handoff(task)
        return f"{base}\n\n{handoff}" if handoff else base
