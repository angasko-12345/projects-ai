"""Workflow and task-graph construction and validation."""

from __future__ import annotations

from uuid import uuid4

from ..tasks import Task
from .support import STANDARD_TASK_ROLES


class PlanningMixin:
    """WorkflowEngine methods: workflow and task-graph construction and validation."""

    def create_standard_workflow(self, description: str) -> tuple[str, list[Task]]:
        workflow_id = self.state.create_workflow(description)
        plan = self.state.add_task(
            Task(
                f"Plan a safe implementation for: {description}",
                STANDARD_TASK_ROLES[0],
                workflow_id,
                max_attempts=self.config.max_attempts,
            )
        )
        implementation = self.state.add_task(
            Task(
                f"Implement: {description}",
                STANDARD_TASK_ROLES[1],
                workflow_id,
                dependencies=(plan.id,),
                max_attempts=self.config.max_attempts,
            )
        )
        verification = self.state.add_task(
            Task(
                "Run configured project verification commands.",
                STANDARD_TASK_ROLES[2],
                workflow_id,
                dependencies=(implementation.id,),
                max_attempts=1,
            )
        )
        # The reviewer judges the implementation, so it needs the implementation
        # result, not only the verification transcript. Both are PASSED before
        # review can be scheduled, so this adds context, not a gate.
        review = self.state.add_task(
            Task(
                f"Review the completed change for: {description}",
                STANDARD_TASK_ROLES[3],
                workflow_id,
                dependencies=(verification.id, implementation.id),
                max_attempts=self.config.max_attempts,
            )
        )
        tasks = [plan, implementation, verification, review]
        if getattr(self.config, "security_review", False):
            security = self.state.add_task(
                Task(
                    f"Security-review the completed change for: {description}",
                    "security",
                    workflow_id,
                    dependencies=(review.id, implementation.id),
                    max_attempts=self.config.max_attempts,
                )
            )
            tasks.append(security)
        return workflow_id, tasks

    def create_workflow(
        self, description: str, specifications: list[dict[str, object]]
    ) -> tuple[str, list[Task]]:
        """Create a user-defined dependency graph from a validated workflow file."""
        self.validate_workflow_specifications(specifications)
        identifiers: dict[str, str] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            identifiers[identifier] = str(uuid4())
        workflow_id = self.state.create_workflow(description)
        tasks: list[Task] = []
        try:
            for index, specification in enumerate(specifications):
                identifier = specification.get("id", f"task-{index + 1}")
                description_value = specification["description"]
                role = specification["role"]
                dependencies = specification.get("dependencies", [])
                task = Task(
                    description_value,
                    role,
                    workflow_id,
                    id=identifiers[identifier],
                    dependencies=tuple(identifiers[item] for item in dependencies),
                    max_attempts=self.config.max_attempts,
                )
                tasks.append(self.state.add_task(task))
        except Exception:
            # Each StateStore write commits independently. If persistence fails
            # midway, remove the workflow and any tasks already inserted so a
            # retry never sees a half-created graph.
            self.state.delete_workflow(workflow_id)
            raise
        return workflow_id, tasks

    @classmethod
    def validate_workflow_specifications(
        cls,
        specifications: list[dict[str, object]],
    ) -> None:
        """Validate all task fields and graph references before any side effect."""
        if not isinstance(specifications, list) or not all(
            isinstance(specification, dict) for specification in specifications
        ):
            raise ValueError("Workflow tasks must be a list of mappings.")
        identifiers: dict[str, str] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError("Each workflow task needs a unique string id.")
            if not isinstance(specification.get("description"), str) or not isinstance(
                specification.get("role"), str
            ):
                raise ValueError("Each workflow task requires string description and role fields.")
            dependencies = specification.get("dependencies", [])
            if not isinstance(dependencies, list) or not all(
                isinstance(item, str) for item in dependencies
            ):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            identifiers[identifier] = identifier
        cls._validate_acyclic(specifications, identifiers)

    @staticmethod
    def _validate_acyclic(
        specifications: list[dict[str, object]], identifiers: dict[str, str]
    ) -> None:
        dependencies_by_id: dict[str, set[str]] = {}
        for index, specification in enumerate(specifications):
            identifier = specification.get("id", f"task-{index + 1}")
            dependencies = specification.get("dependencies", [])
            if (
                not isinstance(identifier, str)
                or not isinstance(dependencies, list)
                or not all(isinstance(item, str) for item in dependencies)
            ):
                raise ValueError("Workflow task dependencies must be a list of task ids.")
            if identifier in dependencies:
                raise ValueError(f"Workflow task '{identifier}' cannot depend on itself.")
            dependencies_by_id[identifier] = set(dependencies)
        unknown = sorted(
            {
                dependency
                for dependencies in dependencies_by_id.values()
                for dependency in dependencies
                if dependency not in identifiers
            }
        )
        if unknown:
            raise ValueError(f"Unknown task dependencies: {', '.join(unknown)}")
        resolved: set[str] = set()
        pending = dict(dependencies_by_id)
        while pending:
            ready = [
                identifier
                for identifier, dependencies in pending.items()
                if dependencies <= resolved
            ]
            if not ready:
                raise ValueError("Workflow task dependencies contain a cycle.")
            for identifier in ready:
                resolved.add(identifier)
                pending.pop(identifier)
