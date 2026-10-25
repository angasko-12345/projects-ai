#!/usr/bin/env python
"""Acceptance tests for AgentOps as described in Issue #4.

Drives the real ``WorkflowEngine.run_high_level`` end to end with a scripted
agent runner and verifier, and checks the four Issue #4 behaviours:

1. clean success reaches READY with every task PASSED;
2. a failing verification is repaired and re-verified before READY;
3. a review that REJECTS the change is repaired and re-reviewed before READY;
4. a cancelled agent run never reaches READY.

The scripted agents speak the current output contract (a structured
AgentResult JSON object, and a structured verdict line for review), because
the engine fails closed on anything else since the Issue #12 gates. Every
scenario runs in its own throwaway git repository, never the caller's
checkout. Exit status is 0 only when every scenario passes.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentops"))
from agentops.agent_result import AgentResult, AgentResultStatus  # noqa: E402
from agentops.config import AgentConfig, AppConfig  # noqa: E402
from agentops.agent_run import AgentRunMetadata  # noqa: E402
from agentops.registry import DetectedAgent  # noqa: E402
from agentops.runner import RunResult  # noqa: E402
from agentops.state import StateStore  # noqa: E402
from agentops.tasks import TaskStatus  # noqa: E402
from agentops.workflow import WorkflowEngine  # noqa: E402

ROLES = ("architecture", "implementation", "review", "debugging")

APPROVE = json.dumps({
    "schema_version": 1, "verdict": "approve", "blocking": [],
    "non_blocking": [], "confidence": 0.99,
})
REJECT = json.dumps({
    "schema_version": 1, "verdict": "reject",
    "blocking": ["multiply() ignores negative operands"],
    "non_blocking": [], "confidence": 0.9,
})


def create_test_config():
    agent = AgentConfig("fallback", "fake", ("{prompt}",), roles=ROLES, enabled=True)
    return AppConfig(
        {"fallback": agent},
        {role: ("fallback",) for role in ROLES},
        (("test",),),
        max_attempts=2,
        concurrency=1,
        max_repair_cycles=1,
    )


def success_payload(summary="done"):
    return json.dumps(AgentResult(status=AgentResultStatus.SUCCESS, summary=summary).to_dict())


def create_scripted_runner(state, review_verdicts=(), cancelled=False):
    """Agent double. Review tasks emit the next scripted verdict (then APPROVE);
    every other role emits a structured SUCCESS result. Records each call."""
    verdicts = list(review_verdicts)
    calls = []

    async def run_agent(agent, prompt, directory, task_id, cancel_event=None):
        role = state.get_task(task_id).role
        calls.append(role)
        if cancelled:
            stdout = "interrupted"
        elif role == "review":
            stdout = verdicts.pop(0) if verdicts else APPROVE
        else:
            stdout = success_payload(f"{role} done")
        return RunResult(
            agent.config.name, ("fake",), 0, stdout, "", 0.01, False,
            Path(f"{task_id}.log"), cancelled=cancelled,
        )

    return run_agent, calls


def create_scripted_verifier(outcomes):
    """Verifier double: one outcome per call (True = pass); the last repeats.
    Builds a fresh coroutine per call, so it survives repeated verification."""
    script = list(outcomes)
    calls = []

    async def run(*_args, **_kwargs):
        ok = script.pop(0) if len(script) > 1 else script[0]
        calls.append(ok)
        return [MagicMock(succeeded=ok, exit_code=0 if ok else 1,
                          output="tests passed" if ok else "one test failed")]

    verifier = MagicMock()
    verifier.run = run
    return verifier, calls


def make_git_repo():
    directory = Path(tempfile.mkdtemp(prefix="agentops-acceptance-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "acceptance@agentops.invalid"],
                ["git", "config", "user.name", "AgentOps Acceptance"]):
        subprocess.run(cmd, cwd=directory, check=True, env=env)
    (directory / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=directory, check=True, env=env)
    return directory


def run_scenario(description, review_verdicts=(), verifications=(True,), cancelled=False):
    config = create_test_config()
    state = StateStore(":memory:")
    registry = MagicMock()
    registry.select.return_value = DetectedAgent(config.agents["fallback"], True, "fake")
    runner = MagicMock()
    runner.run_agent, agent_calls = create_scripted_runner(state, review_verdicts, cancelled)
    verifier, verify_calls = create_scripted_verifier(verifications)
    engine = WorkflowEngine(
        config, state, registry, runner, verifier,
        metadata_collector=MagicMock(return_value=AgentRunMetadata(files_changed=("calc.py",))),
    )
    repo = make_git_repo()
    try:
        result = asyncio.run(engine.run_high_level(description, repo))
        tasks = state.list_tasks(result.workflow_id)
    finally:
        state.close()
        shutil.rmtree(repo, ignore_errors=True)
    print(f"Ready: {result.ready}  Summary: {result.summary}")
    print(f"Agent calls: {agent_calls}")
    print(f"Verification runs: {verify_calls}")
    for task in tasks:
        print(f"  {task.role}: {task.status.value}")
    return result, tasks, agent_calls, verify_calls


def banner(title):
    print("\n" + "=" * 80 + f"\n{title}\n" + "=" * 80)


def test_clean_success():
    banner("TEST 1: CLEAN SUCCESS")
    result, tasks, calls, verifies = run_scenario("Add a multiply function")
    return (
        result.ready
        and bool(tasks)
        and all(t.status == TaskStatus.PASSED for t in tasks)
        and {"architecture", "implementation", "review"} <= set(calls)
        and "debugging" not in calls
        and verifies == [True]
    )


def test_verification_failure_and_repair():
    banner("TEST 2: VERIFICATION FAILURE AND REPAIR")
    result, _tasks, calls, verifies = run_scenario(
        "Fix calculator", verifications=(False, True))
    # Failing verification must trigger a debugging repair, then a passing
    # re-verification, before the workflow may be READY.
    return result.ready and "debugging" in calls and verifies[:2] == [False, True]


def test_review_rejection_and_repair():
    banner("TEST 3: REVIEW REJECTION AND REPAIR")
    result, _tasks, calls, _verifies = run_scenario(
        "Feature with comments", review_verdicts=(REJECT,))
    # A REJECT verdict must lead to a repair and a second, approving review.
    first_review = calls.index("review") if "review" in calls else -1
    repaired_after_reject = first_review >= 0 and any(
        role in ("debugging", "implementation") for role in calls[first_review + 1:])
    return result.ready and calls.count("review") >= 2 and repaired_after_reject


def test_review_rejection_without_repair_is_not_ready():
    banner("TEST 3b: PERSISTENT REVIEW REJECTION")
    result, tasks, _calls, _verifies = run_scenario(
        "Feature reviewers never accept", review_verdicts=(REJECT, REJECT, REJECT, REJECT))
    return not result.ready and any(t.status == TaskStatus.FAILED for t in tasks)


def test_cancellation():
    banner("CANCELLATION TEST")
    result, tasks, _calls, _verifies = run_scenario("Cancelled task", cancelled=True)
    return (
        not result.ready
        and not any(t.status == TaskStatus.PASSED for t in tasks)
    )


SCENARIOS = [
    ("TEST 1: Clean Success", test_clean_success),
    ("TEST 2: Verification Failure and Repair", test_verification_failure_and_repair),
    ("TEST 3: Review Rejection and Repair", test_review_rejection_and_repair),
    ("TEST 3b: Persistent Rejection Not Ready", test_review_rejection_without_repair_is_not_ready),
    ("Cancellation Test", test_cancellation),
]


def main():
    print("AgentOps Acceptance Tests - Issue #4")
    results = []
    for name, func in SCENARIOS:
        try:
            ok = bool(func())
        except Exception as exc:  # a crash is a failed scenario, never a skip
            print(f"ERROR: {type(exc).__name__}: {exc}")
            ok = False
        print(f"\n[{'PASS' if ok else 'FAIL'}] {name}")
        results.append((name, ok))
    banner("SUMMARY")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"{name:<45} {'PASS' if ok else 'FAIL'}")
    print(f"\nTOTAL: {passed}/{len(results)} tests passed")
    if passed == len(results):
        print("\nALL TESTS PASSED - AgentOps behaves as described in Issue #4")
        return 0
    print(f"\n{len(results) - passed} test(s) failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
