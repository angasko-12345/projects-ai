"""Evidence checks, verification evidence and workflow readiness."""

from __future__ import annotations


from ..agent_run import GitRunMetadataCollector
from ..evidence import TaskEvidence, expected_evidence
from ..execution_model import (
    WorkflowReadiness,
    assess_workflow_readiness,
)
from ..tasks import Task
from .support import _is_git_working_tree


class ReadinessMixin:
    """WorkflowEngine methods: evidence checks, verification evidence and workflow readiness."""

    def _evidence_satisfied(self, task: Task, result, working_directory) -> tuple[bool, str]:
        """Apply the role's evidence contract to a successful agent run.

        Measures the working tree directly instead of trusting the persisted
        AgentRunOutcome. Two reasons: `RunResult` does not carry `files_changed`
        at all, and a runner double that accepts but ignores the metadata
        collector still produces a row whose empty `files_changed` means "not
        measured" rather than "nothing changed". Measuring here makes the check
        independent of who ran the agent.
        """
        requirement = expected_evidence(task.role)
        if requirement is not TaskEvidence.WORKTREE_CHANGE:
            return True, ""
        try:
            collector = self.metadata_collector or GitRunMetadataCollector()
            metadata = collector(working_directory)
        except Exception:
            # Could not measure: report satisfied and let verification decide,
            # rather than failing work on a measurement failure.
            return True, ""
        if not _is_git_working_tree(working_directory):
            # The collector reads `git status`, so outside a repository it
            # reports nothing at all. That is "cannot observe", not "observed
            # empty" -- conflating the two would fail every workflow run
            # against a plain directory, which several tests and some real
            # invocations do.
            return True, ""
        if metadata.files_changed:
            return True, ""
        return False, (
            "no_evidence: the agent exited cleanly but changed no files in "
            f"{working_directory}, so it did not do the work it was asked to do"
        )

    def _verification_source_run(self, task: Task) -> str | None:
        candidates = []
        for dependency in task.dependencies:
            try:
                run = self.state.latest_agent_run(task.workflow_id, dependency)
            except Exception:
                continue
            if run is not None:
                candidates.append(run)
        if not candidates:
            return None
        return max(candidates, key=lambda run: (run.created_at, run.id)).id

    def verification_evidence(self, workflow_id: str) -> list[Task]:
        """Return verification tasks backed by passed verification evidence.

        Kernel path: verified flag plus a linked VerificationReport run.
        Legacy path: verified flag plus the command-output transcript (the
        output IS the evidence — no run object exists there).  Single
        definition used by READY gating everywhere (Track A1).
        """
        return [
            task
            for task in self.state.list_tasks(workflow_id)
            if task.role == "verification"
            and task.verified
            and (task.verification_run_id or (task.result or "").strip())
        ]

    def workflow_readiness(
        self,
        workflow_id: str,
        *,
        verification_task_id: str | None = None,
        review_task_id: str | None = None,
    ) -> WorkflowReadiness:
        """Assess the authoritative READY contract for one workflow.

        Every entry point that can declare a workflow READY — the CLI custom-DAG
        path, the GUI, and :meth:`run_high_level` — assesses readiness through
        this method, so no path can hold a weaker contract than the standard
        flow.
        """
        return assess_workflow_readiness(
            self.state.list_tasks(workflow_id),
            workflow_id,
            verification_task_id=verification_task_id,
            review_task_id=review_task_id,
        )
