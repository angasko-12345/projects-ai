"""Regression tests for senior-review findings (Codex fixes)."""
import asyncio
import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.config import AppConfig, AgentConfig, load_config
from agentops.logging import LogManager, _redact
from agentops.registry import AgentRegistry
from agentops.runner import AgentRunner, RunResult
from agentops.state import StateStore
from agentops.tasks import Task
from agentops.verification import Verifier
from agentops.workflow import WorkflowEngine


def _engine(**overrides):
    defaults = dict(max_attempts=2, concurrency=2, max_repair_cycles=1)
    defaults.update(overrides)
    config = AppConfig(
        {"fallback": AgentConfig("fallback", "fake", ("{prompt}",),
                                 ("architecture", "implementation", "review", "debugging"))},
        {role: ("fallback",) for role in ("architecture", "implementation", "review", "debugging")},
        (("test",),),
        max_attempts=defaults["max_attempts"], concurrency=defaults["concurrency"],
        max_repair_cycles=defaults["max_repair_cycles"],
    )
    state = StateStore(":memory:")
    registry = MagicMock()
    from agentops.registry import DetectedAgent
    registry.select.return_value = DetectedAgent(config.agents["fallback"], True, "fake")
    runner = MagicMock()

    async def _run_agent(agent, prompt, directory, task_id, cancel_event=None):
        return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

    runner.run_agent = _run_agent
    verifier = MagicMock()
    return config, state, registry, runner, verifier


class VerificationRegressions(unittest.TestCase):
    def test_empty_verification_commands_pass(self):
        """Review #2: Verifier([]) must not fail the verification task."""
        async def go():
            return await Verifier((), 5).run(".")
        self.assertEqual(asyncio.run(go()), [])

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None):
            return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

        config = AppConfig(
            {"a": AgentConfig("a", "fake", ("{prompt}",), ("implementation",))},
            {"implementation": ("a",)}, (), max_attempts=1, concurrency=1,
        )
        state = StateStore(":memory:")
        try:
            from agentops.registry import DetectedAgent
            registry = MagicMock()
            registry.select.return_value = DetectedAgent(config.agents["a"], True, "fake")
            runner = MagicMock()
            runner.run_agent = _run_agent
            engine = WorkflowEngine(config, state, registry, runner, Verifier(()))
            wid = state.create_workflow("w")
            task = state.add_task(Task("check", "verification", wid, max_attempts=1))
            asyncio.run(engine.execute(wid, Path.cwd()))
            from agentops.tasks import TaskStatus
            self.assertEqual(state.get_task(task.id).status, TaskStatus.PASSED)
        finally:
            state.close()


class DependencyRegressions(unittest.TestCase):
    def test_self_dependency_rejected(self):
        config, state, registry, runner, verifier = _engine()
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            with self.assertRaises(ValueError):
                engine.create_workflow("w", [
                    {"id": "a", "description": "x", "role": "implementation", "dependencies": ["a"]},
                ])
        finally:
            state.close()

    def test_cycle_rejected(self):
        config, state, registry, runner, verifier = _engine()
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            with self.assertRaises(ValueError):
                engine.create_workflow("w", [
                    {"id": "a", "description": "x", "role": "implementation", "dependencies": ["b"]},
                    {"id": "b", "description": "y", "role": "implementation", "dependencies": ["a"]},
                ])
        finally:
            state.close()

    def test_unknown_dependency_rejected(self):
        config, state, registry, runner, verifier = _engine()
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            with self.assertRaises(ValueError):
                engine.create_workflow("w", [
                    {"id": "a", "description": "x", "role": "implementation", "dependencies": ["nope"]},
                ])
        finally:
            state.close()


class RepairCycleRegressions(unittest.TestCase):
    def test_zero_repair_cycles_adds_no_tasks(self):
        config, state, registry, runner, verifier = _engine(max_repair_cycles=0)
        try:
            failed = MagicMock(succeeded=False, output="boom")
            verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[failed]))
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            result = asyncio.run(engine.run_high_level("Fix", Path.cwd()))
            self.assertFalse(result.ready)
            self.assertEqual(len(state.list_tasks(result.workflow_id)), 4)
        finally:
            state.close()

    def test_two_repair_cycles_retry_twice(self):
        config, state, registry, runner, verifier = _engine(max_repair_cycles=2)
        try:
            failed = MagicMock(succeeded=False, output="boom")
            verifier.run = MagicMock(side_effect=[
                asyncio.sleep(0, result=[failed]),
                asyncio.sleep(0, result=[failed]),
                asyncio.sleep(0, result=[failed]),
            ])
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            result = asyncio.run(engine.run_high_level("Fix", Path.cwd()))
            self.assertFalse(result.ready)
            self.assertEqual(len(state.list_tasks(result.workflow_id)), 4 + 2 * 3)
        finally:
            state.close()


class ConfigRegressions(unittest.TestCase):
    def test_unknown_role_preference_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json
            json.dump({"agents": {"a": {"command": "x", "args": ["{prompt}"]}},
                       "role_preferences": {"implementation": ["ghost"]}}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_empty_args_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json
            json.dump({"agents": {"a": {"command": "x", "args": []}}}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_missing_prompt_warns(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json
            json.dump({"agents": {"a": {"command": "x", "args": ["--fixed"]}}}, handle)
            path = handle.name
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                load_config(path)
            self.assertTrue(any("{prompt}" in str(item.message) for item in caught))
        finally:
            os.unlink(path)

    def test_non_dict_verification_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json
            json.dump({"agents": {}, "verification": ["nope"]}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_registry_select_skips_unknown_preferred(self):
        from agentops.registry import DetectedAgent
        config = AppConfig(
            {"a": AgentConfig("a", "fake", ("{prompt}",), ("implementation",))},
            {"implementation": ("ghost", "a")}, (), max_attempts=1, concurrency=1,
        )
        registry = AgentRegistry(config)
        registry._detected = {"a": DetectedAgent(config.agents["a"], True, "/bin/fake")}
        selected = registry.select("implementation")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.config.name, "a")


class StateRegressions(unittest.TestCase):
    def test_event_commits_without_outer_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "state.sqlite")
            store = StateStore(db)
            wid = store.create_workflow("w")
            store.event(wid, None, "note", "hello")
            store.close()
            reopened = StateStore(db)
            try:
                rows = reopened.connection.execute("SELECT * FROM events WHERE detail='hello'").fetchall()
                self.assertEqual(len(rows), 1)
            finally:
                reopened.close()

    def test_latest_workflow_uses_insertion_order(self):
        store = StateStore(":memory:")
        try:
            first = store.create_workflow("first")
            second = store.create_workflow("second")
            latest = store.latest_workflow()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["id"], second)
            self.assertNotEqual(first, second)
        finally:
            store.close()


class CliRegressions(unittest.TestCase):
    def test_missing_workflow_file_returns_2(self):
        import io
        from contextlib import redirect_stdout
        from agentops.cli import main
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["workflow", "does-not-exist-12345.yaml"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR", output.getvalue())


class SecurityRegressions(unittest.TestCase):
    def test_environment_passthrough(self):
        os.environ["AGENTOPS_TEST_ALLOW_ME"] = "yes"
        try:
            env = AgentRunner._environment(("AGENTOPS_TEST_ALLOW_ME",), ())
            self.assertEqual(env.get("AGENTOPS_TEST_ALLOW_ME"), "yes")
            env2 = AgentRunner._environment((), ("AGENTOPS_TEST_PREFIX_",))
            os.environ["AGENTOPS_TEST_PREFIX_SECRET"] = "1"
            try:
                env2 = AgentRunner._environment((), ("AGENTOPS_TEST_PREFIX_",))
                self.assertEqual(env2.get("AGENTOPS_TEST_PREFIX_SECRET"), "1")
            finally:
                del os.environ["AGENTOPS_TEST_PREFIX_SECRET"]
            env3 = AgentRunner._environment()
            self.assertNotIn("AGENTOPS_TEST_ALLOW_ME", env3)
        finally:
            del os.environ["AGENTOPS_TEST_ALLOW_ME"]

    def test_logs_redact_secrets(self):
        self.assertIn("[REDACTED]", _redact("api_key=sk-abcdef1234567890"))
        self.assertIn("[REDACTED]", _redact("token: ghp_12345678901234567890"))

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_log_files_restricted(self):
        import stat
        with tempfile.TemporaryDirectory() as directory:
            manager = LogManager(Path(directory) / "logs")
            path = manager.write_run("t1", "agent", "out", "err", "meta")
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)


class ClaimRegressions(unittest.TestCase):
    """Review: ready_tasks() + execute() must atomically claim tasks."""

    def test_claim_transitions_pending_to_running(self):
        _, state, _, _, _ = _engine()
        try:
            wid = state.create_workflow("w")
            task = state.add_task(Task("work", "implementation", wid))
            claimed = state.claim_task(task.id)
            self.assertIsNotNone(claimed)
            assert claimed is not None
            from agentops.tasks import TaskStatus
            self.assertEqual(claimed.status, TaskStatus.RUNNING)
            self.assertEqual(claimed.attempts, 1)
            self.assertIsNotNone(claimed.started_at)
        finally:
            state.close()

    def test_second_claim_returns_none(self):
        from agentops.tasks import TaskStatus
        _, state, _, _, _ = _engine()
        try:
            wid = state.create_workflow("w")
            task = state.add_task(Task("work", "implementation", wid))
            self.assertIsNotNone(state.claim_task(task.id))
            self.assertIsNone(state.claim_task(task.id))
            finished = state.get_task(task.id)
            finished.status = TaskStatus.PASSED
            state.update_task(finished)
            self.assertIsNone(state.claim_task(task.id))
        finally:
            state.close()

    def test_execute_skips_already_claimed_task(self):
        """A task claimed by another worker must not run twice."""
        from agentops.tasks import TaskStatus
        config, state, registry, runner, verifier = _engine()
        calls: list[str] = []

        async def _run_agent(agent, prompt, directory, task_id, cancel_event=None):
            calls.append(task_id)
            return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

        runner.run_agent = _run_agent
        try:
            engine = WorkflowEngine(config, state, registry, runner, verifier)
            wid = state.create_workflow("w")
            task = state.add_task(Task("work", "implementation", wid))
            self.assertIsNotNone(state.claim_task(task.id))  # another worker wins
            asyncio.run(engine._execute_task(task, Path.cwd()))
            self.assertEqual(calls, [])
            self.assertEqual(state.get_task(task.id).status, TaskStatus.RUNNING)
        finally:
            state.close()

    def test_concurrent_claims_single_winner(self):
        import threading
        _, state, _, _, _ = _engine()
        try:
            wid = state.create_workflow("w")
            task = state.add_task(Task("work", "implementation", wid))
            results: list = []
            threads = [threading.Thread(target=lambda: results.append(state.claim_task(task.id)))
                       for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(1 for result in results if result is not None), 1)
        finally:
            state.close()

    def test_claim_across_connections_single_winner(self):
        """Two handles on the same database file (separate processes model)."""
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.sqlite")
            first = StateStore(database)
            second = StateStore(database)
            try:
                wid = first.create_workflow("w")
                task = first.add_task(Task("work", "implementation", wid))
                winners = [store.claim_task(task.id) for store in (first, second)]
                self.assertEqual(sum(1 for winner in winners if winner is not None), 1)
            finally:
                first.close()
                second.close()


class ExecutableRegressions(unittest.TestCase):
    """Review: run the binary resolved at detection time, not a PATH re-lookup."""

    def test_build_command_prefers_resolved_executable(self):
        from agentops.registry import DetectedAgent
        config = AgentConfig("codex", "codex", ("exec", "{prompt}"), ("implementation",))
        agent = DetectedAgent(config, True, "/resolved/path/codex")
        command = AgentRunner.build_command(agent, "do it")
        self.assertEqual(command[0], "/resolved/path/codex")
        self.assertIn("do it", command)

    def test_build_command_falls_back_to_command(self):
        from agentops.registry import DetectedAgent
        config = AgentConfig("codex", "codex", ("exec", "{prompt}"), ("implementation",))
        self.assertEqual(AgentRunner.build_command(DetectedAgent(config, True, None), "do it")[0], "codex")
        self.assertEqual(AgentRunner.build_command(config, "do it")[0], "codex")


class StateLocationRegressions(unittest.TestCase):
    """Review: state/logs and worktrees must share one canonical root."""

    @unittest.skipIf(__import__("shutil").which("git") is None, "git is required")
    def test_nested_directory_resolves_repository_root(self):
        import subprocess
        from agentops.cli import _resolve_state_base
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init"], cwd=root, check=True,
                           capture_output=True)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            self.assertEqual(_resolve_state_base(nested), root.resolve())

    def test_non_git_directory_falls_back(self):
        from agentops.cli import _resolve_state_base
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_resolve_state_base(Path(directory)), Path(directory))

    def test_workflow_parser_accepts_cwd(self):
        from agentops.cli import build_parser
        args = build_parser().parse_args(["workflow", "flow.yaml"])
        self.assertEqual(args.cwd, Path.cwd())
        args = build_parser().parse_args(["workflow", "flow.yaml", "--cwd", "somewhere"])
        self.assertEqual(args.cwd, Path("somewhere"))


class RunnerEnvironmentRegressions(unittest.TestCase):
    """Bun-based agent CLIs die without %SystemRoot%; never launch them bare."""

    def test_system_root_fallback_when_parent_env_scrubbed(self):
        with patch.dict(os.environ, {}, clear=True):
            env = AgentRunner._environment()
        if os.name == "nt":
            self.assertEqual(env["SystemRoot"], r"C:\WINDOWS")
            self.assertEqual(env["WINDIR"], r"C:\WINDOWS")
        else:
            self.assertNotIn("SystemRoot", env)

    def test_existing_system_root_is_preserved(self):
        with patch.dict(os.environ, {"SystemRoot": "D:\\Win", "WINDIR": "D:\\Win"}):
            env = AgentRunner._environment()
        if os.name == "nt":
            self.assertEqual(env["SystemRoot"], "D:\\Win")
            self.assertEqual(env["WINDIR"], "D:\\Win")

    @unittest.skipIf(os.name != "nt", "Windows casing behaviour")
    def test_uppercase_parent_vars_pass_through(self):
        """Parent envs often carry SYSTEMROOT/PATH in all caps; the child
        must still receive them under canonical names."""
        with patch.dict(os.environ, {"SYSTEMROOT": "C:\\WINDOWS", "PATH": "C:\\bin"}, clear=True):
            env = AgentRunner._environment()
        self.assertEqual(env["SystemRoot"], "C:\\WINDOWS")
        self.assertEqual(env["PATH"], "C:\\bin")


class NoAgentFallbackRegressions(unittest.TestCase):
    """A retry that exhausts all agents must say which ones were tried."""

    def test_retry_without_alternative_names_tried_agent(self):
        from agentops.registry import DetectedAgent
        config, state, _, _, verifier = _engine()
        registry = MagicMock()
        registry.select.side_effect = [
            DetectedAgent(config.agents["fallback"], True, "fake"),
            None,
        ]

        async def _fail(agent, prompt, directory, task_id):
            return RunResult(agent.config.name, ("fake",), 1, "", "boom", 0.01, False, Path(f"{task_id}.log"))

        runner = MagicMock()
        runner.run_agent = _fail
        engine = WorkflowEngine(config, state, registry, runner, verifier)
        try:
            wid = state.create_workflow("w")
            task = state.add_task(Task("work", "implementation", wid, max_attempts=2))
            asyncio.run(engine.execute(wid, Path.cwd()))
            final = state.get_task(task.id)
            from agentops.tasks import TaskStatus
            self.assertEqual(final.status, TaskStatus.FAILED)
            self.assertIn("fallback", final.result or "")
            self.assertIn("already tried", final.result or "")
            kinds = [row["kind"] for row in state.list_events(wid, task.id)]
            self.assertIn("no-agent-fallback", kinds)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
