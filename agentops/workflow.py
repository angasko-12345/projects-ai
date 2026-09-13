"""Dependency-aware task orchestration with agent fallback and repairs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .registry import AgentRegistry
from .runner import AgentRunner
from .state import StateStore
from .tasks import Task, TaskStatus, utc_now
from .verification import Verifier


@dataclass(frozen=True)
class WorkflowResult:
    workflow_id: str
    ready: bool
    summary: str


class WorkflowEngine:
    def __init__(self, config: AppConfig, state: StateStore, registry: AgentRegistry,
                 runner: AgentRunner, verifier: Verifier):
        self.config = config
        self.state = state
        self.registry = registry
        self.runner = runner
        self.verifier = verifier

    def create_standard_workflow(self, description: str) -> tuple[str, list[Task]]:
        workflow_id = self.state.create_workflow(description)
        plan = self.state.add_task(Task(f"Plan a safe implementation for: {description}", "architecture", workflow_id,
                                        max_attempts=self.config.max_attempts))
        implementation = self.state.add_task(Task(f"Implement: {description}", "implementation", workflow_id,
                                                  dependencies=(plan.id,), max_attempts=self.config.max_attempts))
        verification = self.state.add_task(Task("Run configured project verification commands.", "verification", workflow_id,
                                                dependencies=(implementation.id,), max_attempts=1))
        review = self.state.add_task(Task(f"Review the completed change for: {description}", "review", workflow_id,
                                          dependencies=(verification.id,), max_attempts=self.config.max_attempts))
        return workflow_id, [plan, implementation, verification, review]

    async def execute(self, workflow_id: str, working_directory: str | Path) -> None:
        semaphore = asyncio.Semaphore(self.config.concurrency)
        while ready := self.state.ready_tasks(workflow_id):
            async def run_limited(task: Task) -> None:
                async with semaphore:
                    await self._execute_task(task, working_directory)
            await asyncio.gather(*(run_limited(task) for task in ready))
        self.state.refresh_workflow_status(workflow_id)

    async def _execute_task(self, task: Task, working_directory: str | Path) -> None:
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.started_at = utc_now()
        self.state.update_task(task)
        if task.role == "verification":
            results = await self.verifier.run(working_directory)
            succeeded = bool(results) and all(result.succeeded for result in results)
            task.result = "\n".join(result.output for result in results)
            task.status = TaskStatus.PASSED if succeeded else TaskStatus.FAILED
        else:
            excluded = {task.assigned_agent} if task.assigned_agent and task.attempts > 1 else set()
            agent = self.registry.select(task.role, excluded)
            if agent is None:
                task.status = TaskStatus.FAILED
                task.result = f"No installed agent supports role '{task.role}'."
            else:
                task.assigned_agent = agent.config.name
                prompt = self._prompt(task, working_directory)
                try:
                    result = await self.runner.run_agent(agent, prompt, working_directory, task.id)
                    task.result = f"log={result.log_path}\n{result.stdout}\n{result.stderr}".strip()
                    task.status = TaskStatus.PASSED if result.succeeded else TaskStatus.FAILED
                except (OSError, RuntimeError) as error:
                    task.status = TaskStatus.FAILED
                    task.result = f"Agent execution error: {error}"
        if task.status is TaskStatus.FAILED and task.attempts < task.max_attempts:
            task.status = TaskStatus.PENDING
            task.result = f"Attempt {task.attempts} failed; retrying.\n{task.result}"
        if task.status in {TaskStatus.PASSED, TaskStatus.FAILED}:
            task.finished_at = utc_now()
        self.state.update_task(task)

    @staticmethod
    def _prompt(task: Task, working_directory: str | Path) -> str:
        return (
            f"You are the {task.role} agent for AgentOps task {task.id}.\n"
            f"Workspace: {Path(working_directory).resolve()}\n"
            f"Request: {task.description}\n"
            "Work only in this workspace. Do not read secrets or alter AgentOps configuration. "
            "Summarize changes and tests in your final response."
        )

    async def run_high_level(self, description: str, working_directory: str | Path) -> WorkflowResult:
        workflow_id, tasks = self.create_standard_workflow(description)
        await self.execute(workflow_id, working_directory)
        verification = self.state.get_task(tasks[2].id)
        if verification.status is TaskStatus.PASSED:
            review = self.state.get_task(tasks[3].id)
            return WorkflowResult(workflow_id, review.status is TaskStatus.PASSED,
                                  "READY" if review.status is TaskStatus.PASSED else "Review did not pass.")
        implementation = self.state.get_task(tasks[1].id)
        if implementation.status is not TaskStatus.PASSED:
            return WorkflowResult(workflow_id, False, "Implementation did not pass; verification was not run.")
        repair = self.state.add_task(Task("Repair the configured verification failure.\n" + (verification.result or ""),
                                          "debugging", workflow_id, dependencies=(implementation.id,),
                                          max_attempts=self.config.max_attempts))
        reverify = self.state.add_task(Task("Re-run configured project verification commands.", "verification", workflow_id,
                                            dependencies=(repair.id,), max_attempts=1))
        final_review = self.state.add_task(Task(f"Review repaired change for: {description}", "review", workflow_id,
                                                dependencies=(reverify.id,), max_attempts=self.config.max_attempts))
        await self.execute(workflow_id, working_directory)
        final_review = self.state.get_task(final_review.id)
        return WorkflowResult(workflow_id, final_review.status is TaskStatus.PASSED,
                              "READY" if final_review.status is TaskStatus.PASSED else "Repair or review did not pass.")
