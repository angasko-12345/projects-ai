"""Autonomy regressions: real handoffs and autonomous review repair.

Two gaps made the standard flow a chain of independent agents rather than one
workflow:

1. `_prompt_with_history` built a prompt from the task description alone. A
   dependent agent never saw the result of the task it depends on, so
   `implementation` could not use the architecture plan and `review` could not
   see what it was reviewing.
2. `run_high_level` returned "Review did not pass." the moment a review failed.
   A demonstrated review rejection -- the one defect the whole chain of agents
   exists to find -- ended the run instead of being repaired, re-verified, and
   re-reviewed within the existing `max_repair_cycles` budget.
"""
import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentops.agent_run import AgentRunMetadata
from agentops.config import AppConfig, AgentConfig
from agentops.registry import DetectedAgent
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import TaskStatus
from agentops.workflow import HANDOFF_RESULT_CHARS, WorkflowEngine


class _Harness(unittest.TestCase):
    """One engine whose fake runner records prompts and can fail chosen roles.

    Prompts are captured per task so a test can assert on what a specific
    agent was actually handed, rather than on an internal helper.
    """

    def setUp(self):
        self.config = AppConfig(
            {"fallback": AgentConfig("fallback", "fake", ("{prompt}",),
                                     ("architecture", "implementation", "review", "debugging"))},
            {role: ("fallback",) for role in
             ("architecture", "implementation", "review", "debugging")},
            (("test",),), max_attempts=2, concurrency=1,
            max_repair_cycles=getattr(self, "repair_cycles", 1),
        )
        self.state = StateStore(":memory:")
        self.registry = MagicMock()
        self.registry.select.return_value = DetectedAgent(
            self.config.agents["fallback"], True, "fake")
        self.prompts: dict[str, str] = {}
        self.review_calls = 0
        self.review_fails_while = lambda: False
        self.runner = MagicMock()
        self.runner.run_agent = self._run_agent
        self.verifier = MagicMock()
        # A fresh coroutine per call: a MagicMock `return_value` hands out the
        # same coroutine object every time and an awaited coroutine cannot be
        # reused, so a second verification would raise instead of running.
        self.verifier.run = MagicMock(
            side_effect=lambda *a, **k: asyncio.sleep(
                0, result=[MagicMock(succeeded=True, output="verification transcript")]))
        self.metadata_collector = MagicMock(
            return_value=AgentRunMetadata(files_changed=("src/app.py",)))

    def tearDown(self):
        self.state.close()

    async def _run_agent(self, agent, prompt, directory, task_id, cancel_event=None):
        self.prompts[task_id] = prompt
        role = self.state.get_task(task_id).role
        if role == "review":
            self.review_calls += 1
            if self.review_fails_while():
                return RunResult(agent.config.name, ("fake",), 1,
                                 "REVIEW_FINDING: missing error handling", "",
                                 0.01, False, Path(f"{task_id}.log"))
        return RunResult(agent.config.name, ("fake",), 0, f"{role} result", "",
                         0.01, False, Path(f"{task_id}.log"))

    def engine(self) -> WorkflowEngine:
        return WorkflowEngine(self.config, self.state, self.registry, self.runner,
                              self.verifier,
                              metadata_collector=self.metadata_collector)

    def prompt_for(self, workflow_id: str, role: str, occurrence: int = 0) -> str:
        matching = [self.state.get_task(task.id) for task in self.state.list_tasks(workflow_id)
                    if task.role == role]
        task = matching[occurrence]
        return self.prompts[task.id]

    def tasks_of(self, workflow_id: str, role: str) -> list:
        return [task for task in self.state.list_tasks(workflow_id) if task.role == role]


class DependencyHandoffTests(_Harness):
    """A dependent agent must receive what its dependency produced."""

    def test_implementation_receives_the_architecture_result(self):
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        prompt = self.prompt_for(result.workflow_id, "implementation")
        self.assertIn("architecture result", prompt,
                      "implementation must receive the architecture result")
        self.assertIn("Role: architecture", prompt)
        self.assertIn("Plan a safe implementation for: Add dark mode", prompt,
                      "the handoff must carry the dependency's description")

    def test_review_receives_implementation_and_verification_context(self):
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        prompt = self.prompt_for(result.workflow_id, "review")
        self.assertIn("implementation result", prompt,
                      "review must receive what it is reviewing")
        self.assertIn("verification transcript", prompt,
                      "review must receive the verification evidence")

    def test_handoff_carries_one_block_per_dependency_with_a_result(self):
        # Review depends on both implementation and verification, so it gets
        # two blocks. An empty result contributes nothing: an empty "Previous
        # task" block would be context with no information in it.
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        plan = self.tasks_of(result.workflow_id, "architecture")[0]
        self.assertTrue(plan.result, "precondition: architecture produced a result")
        self.assertEqual(self.prompt_for(result.workflow_id, "implementation")
                         .count("Previous task:"), 1)
        self.assertEqual(self.prompt_for(result.workflow_id, "review")
                         .count("Previous task:"), 2)

    def test_retry_prompt_still_carries_the_previous_failure(self):
        # The handoff is additive. The existing retry contract -- a failed
        # attempt's evidence reaching the next attempt -- must survive it.
        review_only = AppConfig(
            self.config.agents, self.config.role_preferences, (("test",),),
            max_attempts=2, concurrency=1, max_repair_cycles=1)

        attempts_seen: dict[str, int] = {}
        # Only `implementation` is flaky, so the retry path is exercised without
        # the review-repair cycle taking over the run.
        flaky_role = "implementation"

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None):
            attempts_seen[task_id] = attempts_seen.get(task_id, 0) + 1
            self.prompts[f"{task_id}#{attempts_seen[task_id]}"] = prompt
            role = self.state.get_task(task_id).role
            if role == flaky_role and attempts_seen[task_id] == 1:
                return RunResult(agent.config.name, ("fake",), 1, "", "TRANSIENT BOOM",
                                 0.01, False, Path(f"{task_id}.log"))
            return RunResult(agent.config.name, ("fake",), 0, f"{role} result", "",
                             0.01, False, Path(f"{task_id}.log"))

        self.runner.run_agent = _run_agent
        engine = WorkflowEngine(review_only, self.state, self.registry,
                                self.runner, self.verifier,
                                metadata_collector=self.metadata_collector)
        result = asyncio.run(engine.run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        implementation = self.tasks_of(result.workflow_id, "implementation")[0]
        self.assertEqual(attempts_seen[implementation.id], 2,
                         "precondition: a retry happened")
        retry_prompt = self.prompts[f"{implementation.id}#2"]
        self.assertIn("TRANSIENT BOOM", retry_prompt,
                      "the retry must still inherit the previous failure")
        self.assertIn("architecture result", retry_prompt,
                      "the retry must still inherit the dependency handoff")

    def test_dependency_result_is_bounded(self):
        # Unbounded handoff text is how a prompt becomes larger than the agent's
        # context. The ceiling is enforced here rather than trusted.
        oversized = "x" * (HANDOFF_RESULT_CHARS * 4)

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None):
            self.prompts[task_id] = prompt
            role = self.state.get_task(task_id).role
            stdout = oversized if role == "architecture" else f"{role} result"
            return RunResult(agent.config.name, ("fake",), 0, stdout, "",
                             0.01, False, Path(f"{task_id}.log"))

        self.runner.run_agent = _run_agent
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        prompt = self.prompt_for(result.workflow_id, "implementation")
        self.assertIn("[truncated]", prompt)
        self.assertLess(len(prompt), HANDOFF_RESULT_CHARS * 2,
                        "a truncated dependency result must still fit the budget")


class ReviewRepairAutonomyTests(_Harness):
    """A failed review is a demonstrated defect, so it is repaired."""

    def test_failed_review_creates_a_repair_task(self):
        self.review_fails_while = lambda: self.review_calls <= 2
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        repairs = self.tasks_of(result.workflow_id, "debugging")
        self.assertTrue(repairs, "a failed review must create a repair task")

    def test_repair_task_receives_the_review_failure(self):
        self.review_fails_while = lambda: self.review_calls <= 2
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        repair = self.tasks_of(result.workflow_id, "debugging")[0]
        self.assertIn("REVIEW_FINDING: missing error handling", repair.description)
        self.assertIn("REVIEW_FINDING: missing error handling", self.prompts[repair.id],
                      "the debugging agent must be handed the review failure itself")

    def test_repair_receives_the_finding_when_the_review_exhausts_its_attempts(self):
        # Observed live: the review rejected the change, the retry found no
        # alternative agent, and `task.result` ended up holding the ROUTING
        # error rather than the finding. Repairing from `result` alone would
        # have sent the debugging agent after a nonexistent routing bug. The
        # finding is still on the review task's failure rows, so it must be
        # recovered from there.
        self.review_fails_while = lambda: self.review_calls <= 2
        fallback = DetectedAgent(self.config.agents["fallback"], True, "fake")

        def _select(role, excluded=None, required_capabilities=()):
            # A retry of the only configured agent has nowhere else to go,
            # which is what left `review.result` holding a routing error.
            if excluded:
                return None
            return fallback

        self.registry.select.side_effect = _select
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        review = self.tasks_of(result.workflow_id, "review")[0]
        self.assertIs(review.status, TaskStatus.FAILED)
        self.assertIn("No installed agent supports role", review.result or "",
                      "precondition: the exhausted review's result is the routing error")
        repair = self.tasks_of(result.workflow_id, "debugging")[0]
        self.assertIn("REVIEW_FINDING: missing error handling", repair.description,
                      "the finding must be recovered from the review's failure rows")

    def test_repair_is_followed_by_verification_and_re_review(self):
        self.review_fails_while = lambda: self.review_calls <= 2
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        tasks = self.state.list_tasks(result.workflow_id)
        roles = [task.role for task in tasks]
        self.assertEqual(roles[:4], ["architecture", "implementation",
                                     "verification", "review"])
        self.assertEqual(roles[4:], ["debugging", "verification", "review"],
                         "a review failure must be repaired, re-verified, re-reviewed")

    def test_passing_re_review_reaches_ready(self):
        # The reviewer rejects once (both attempts of its task), then accepts
        # the repaired change. Only the authoritative READY contract may
        # declare success, so this must go through assert_tasks_ready.
        self.review_fails_while = lambda: self.review_calls <= 2
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertTrue(result.ready, result.summary)
        self.assertEqual(result.summary, "READY")
        self.assertIs(self.tasks_of(result.workflow_id, "review")[-1].status,
                      TaskStatus.PASSED)


class ReviewRepairBudgetTests(_Harness):
    """The repair loop is bounded and never invents a pass."""

    repair_cycles = 1

    def test_zero_repair_cycles_creates_no_repair_task(self):
        self.config = AppConfig(
            self.config.agents, self.config.role_preferences, (("test",),),
            max_attempts=2, concurrency=1, max_repair_cycles=0)
        self.review_fails_while = lambda: True
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertFalse(result.ready)
        self.assertEqual(self.tasks_of(result.workflow_id, "debugging"), [])
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 4)

    def test_exhausted_repair_cycles_remain_not_ready(self):
        self.review_fails_while = lambda: True
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertFalse(result.ready, "an unrepaired review rejection is not READY")
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 4 + 3)
        self.assertIsNot(self.tasks_of(result.workflow_id, "review")[-1].status,
                         TaskStatus.PASSED)

    def test_repair_cycles_do_not_exceed_the_configured_budget(self):
        self.config = AppConfig(
            self.config.agents, self.config.role_preferences, (("test",),),
            max_attempts=2, concurrency=1, max_repair_cycles=2)
        self.review_fails_while = lambda: True
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertFalse(result.ready)
        self.assertEqual(len(self.tasks_of(result.workflow_id, "debugging")), 2)
        self.assertEqual(len(self.state.list_tasks(result.workflow_id)), 4 + 2 * 3)

    def test_blocked_review_creates_no_repair_task(self):
        # A BLOCKED review never ran, so nothing about the change was rejected
        # and a debugging task would be invented rather than warranted. This is
        # the review-side twin of the UNVERIFIED rule `run_high_level` already
        # applies to verification: an absent signal is not a failure.
        #
        # The state is assembled directly because a blocked review is not
        # reachable through a clean run -- `ready_tasks` only blocks a review
        # whose dependency failed, and `run_high_level` would have taken the
        # verification-repair branch first.
        engine = self.engine()
        workflow_id, tasks = engine.create_standard_workflow("Add dark mode")
        implementation, verification, review = tasks[1], tasks[2], tasks[3]
        for task in (implementation, verification):
            task.status = TaskStatus.PASSED
            task.result = f"{task.role} result"
            self.state.update_task(task)
        review.status = TaskStatus.BLOCKED
        review.result = "Blocked by an unsuccessful dependency."
        self.state.update_task(review)

        result = asyncio.run(engine._repair_failed_review(
            workflow_id, "Add dark mode", Path.cwd(), None, implementation.id, review))
        self.assertFalse(result.ready)
        self.assertEqual(self.tasks_of(workflow_id, "debugging"), [],
                         "a review that never ran must not trigger a repair")

    def test_repair_that_breaks_verification_is_not_ready(self):
        # The repair cycle is opened for a review defect. If the repair breaks
        # verification, that is a different failure and must not be reported as
        # a successful repair.
        calls = {"verification": 0}
        original = self.verifier.run

        async def _verifier(*args, **kwargs):
            calls["verification"] += 1
            if calls["verification"] > 1:
                return [MagicMock(succeeded=False, output="regressed")]
            return await original(*args, **kwargs)

        self.review_fails_while = lambda: self.review_calls <= 2
        self.verifier.run = MagicMock(side_effect=_verifier)
        result = asyncio.run(self.engine().run_high_level("Add dark mode", Path.cwd()))
        self.assertFalse(result.ready)


if __name__ == "__main__":
    unittest.main()
