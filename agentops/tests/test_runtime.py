"""Shared ProcessRuntime contract tests (Track A3)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentops.config import AgentConfig
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner
from agentops.runtime import OperationCancelled, ProcessResult, ProcessRuntime
from agentops.verification import Verifier
from agentops.verification_kernel import VerificationKernel


class FakeProcess:
    def __init__(self, stdout=b"ok\n", stderr=b"", returncode=0, pid=None):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = pid
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


class HangingProcess:
    def __init__(self, pid=None):
        self.returncode = None
        self.pid = pid
        self.killed = False

    async def communicate(self):
        while not self.killed:
            await asyncio.sleep(0.005)
        return b"partial", b""

    def kill(self):
        self.killed = True


class ProcessRuntimeTests(unittest.TestCase):
    def test_environment_matches_runner_policy(self):
        with patch.dict("os.environ", {"AGENTOPS_RUNTIME_TEST": "yes"}, clear=False):
            runtime_env = ProcessRuntime.build_environment(("AGENTOPS_RUNTIME_TEST",), ())
            runner_env = AgentRunner._environment(("AGENTOPS_RUNTIME_TEST",), ())
        self.assertEqual(runtime_env, runner_env)
        self.assertEqual(runtime_env.get("AGENTOPS_RUNTIME_TEST"), "yes")

    def test_spawn_uses_unified_platform_policy(self):
        with patch("agentops.runtime.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = FakeProcess()
            asyncio.run(ProcessRuntime().spawn_process(
                "prog", cwd=".", env={}, stdout=None, stderr=None,
            ))
        kwargs = spawn.call_args.kwargs
        if sys.platform == "win32":
            import subprocess as stdlib_subprocess
            flags = kwargs.get("creationflags", 0)
            self.assertTrue(flags & stdlib_subprocess.CREATE_NO_WINDOW)
            self.assertTrue(flags & stdlib_subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertTrue(kwargs.get("start_new_session"))
            self.assertNotIn("creationflags", kwargs)

    def test_posix_spawn_policy_is_explicit(self):
        self.assertEqual(
            ProcessRuntime.spawn_options(platform="posix"),
            {"start_new_session": True},
        )

    def test_custom_factory_keeps_existing_spawn_behavior(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append((command, kwargs))
            return FakeProcess()

        asyncio.run(ProcessRuntime(spawn=factory).spawn_process(
            "prog", cwd=".", env={}, stdout=None, stderr=None,
        ))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("start_new_session", calls[0][1])
        self.assertNotIn("creationflags", calls[0][1])

    def test_timeout_terminates_and_reports_partial_output(self):
        async def factory(*command, **kwargs):
            return HangingProcess()

        result = asyncio.run(ProcessRuntime(spawn=factory).run_process(
            ("slow",), cwd=".", env={}, timeout=0.05,
        ))
        self.assertIsInstance(result, ProcessResult)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.cancelled)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stdout, b"partial")

    def test_preset_cancel_event_does_not_spawn(self):
        spawned = []

        async def factory(*command, **kwargs):
            spawned.append(command)
            return FakeProcess()

        cancel_event = threading.Event()
        cancel_event.set()
        result = asyncio.run(ProcessRuntime(spawn=factory).run_process(
            ("prog",), cwd=".", env={}, timeout=30, cancel_event=cancel_event,
        ))
        self.assertTrue(result.cancelled)
        self.assertEqual(spawned, [])

    def test_success_never_needs_a_cancel_watcher_thread(self):
        async def factory(*command, **kwargs):
            return FakeProcess(b"done\n", b"", 0)

        def forbidden(*args, **kwargs):
            raise AssertionError("cancel watcher thread must not be created")

        with patch("agentops.runtime.asyncio.to_thread", new=forbidden):
            result = asyncio.run(ProcessRuntime(spawn=factory).run_process(
                ("prog",), cwd=".", env={}, timeout=30,
                cancel_event=threading.Event(),
            ))
        self.assertFalse(result.cancelled)
        self.assertEqual(result.stdout, b"done\n")

    def test_custom_factory_cleanup_avoids_process_group_kill(self):
        process = HangingProcess(pid=4242)

        async def factory(*command, **kwargs):
            return process

        runtime = ProcessRuntime(spawn=factory)
        with patch("os.killpg", create=True) as killpg:
            stdout, stderr = asyncio.run(runtime.terminate_process(
                process, process_group=False, platform="posix",
            ))
        killpg.assert_not_called()
        self.assertTrue(process.killed)
        self.assertEqual((stdout, stderr), (b"partial", b""))

    def test_default_cleanup_uses_process_group(self):
        process = HangingProcess(pid=4242)

        def fake_killpg(pid, sig):
            process.kill()

        with patch("os.killpg", create=True) as killpg:
            killpg.side_effect = fake_killpg
            stdout, stderr = asyncio.run(ProcessRuntime().terminate_process(
                process, process_group=True, platform="posix",
            ))
        self.assertEqual(killpg.call_count, 1)
        self.assertEqual((stdout, stderr), (b"partial", b""))

    def test_failing_on_running_callback_terminates_process(self):
        process = HangingProcess()

        async def factory(*command, **kwargs):
            return process

        def bad_callback(_process):
            raise RuntimeError("observer blew up")

        with self.assertRaises(RuntimeError):
            asyncio.run(ProcessRuntime(spawn=factory).run_process(
                ("prog",), cwd=".", env={}, timeout=30, on_running=bad_callback,
            ))
        self.assertTrue(process.killed)

    def test_mid_run_cancel_terminates_process(self):
        process = HangingProcess()

        async def factory(*command, **kwargs):
            return process

        cancel_event = threading.Event()
        timer = threading.Timer(0.05, cancel_event.set)
        timer.start()
        try:
            result = asyncio.run(ProcessRuntime(spawn=factory).run_process(
                ("slow",), cwd=".", env={}, timeout=30, cancel_event=cancel_event,
            ))
        finally:
            timer.cancel()
            timer.join(timeout=5)
        self.assertTrue(result.cancelled)
        self.assertTrue(process.killed)

    def test_asyncio_cancellation_terminates_process(self):
        process = HangingProcess()

        async def factory(*command, **kwargs):
            return process

        async def go():
            task = asyncio.create_task(ProcessRuntime(spawn=factory).run_process(
                ("slow",), cwd=".", env={}, timeout=30,
            ))
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(go())
        self.assertTrue(process.killed)

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group behavior")
    def test_posix_terminate_uses_process_group(self):
        process = HangingProcess(pid=4242)
        with patch("os.killpg") as killpg:
            stdout, stderr = asyncio.run(ProcessRuntime().terminate_process(process))
        killpg.assert_called_once()
        called_pid, called_signal = killpg.call_args.args
        self.assertEqual(called_pid, 4242)
        import signal as stdlib_signal
        self.assertEqual(called_signal, stdlib_signal.SIGKILL)
        self.assertEqual((stdout, stderr), (b"partial", b""))

    @unittest.skipIf(sys.platform != "win32", "Windows cleanup behavior")
    def test_windows_taskkill_cleanup_hides_console(self):
        import subprocess as stdlib_subprocess
        process = HangingProcess(pid=4242)
        cleanup = AsyncMock()
        cleanup.wait = AsyncMock(return_value=0)

        async def fake_taskkill(*command, **kwargs):
            process.kill()
            return cleanup

        with patch("agentops.runtime.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = fake_taskkill
            asyncio.run(ProcessRuntime().terminate_process(process))
        kwargs = spawn.call_args.kwargs
        self.assertTrue(kwargs.get("creationflags", 0) & stdlib_subprocess.CREATE_NO_WINDOW)
        self.assertTrue(process.killed)

    def test_factory_alias_is_single_sourced(self):
        from agentops import runtime as runtime_module
        from agentops import verification_kernel as kernel_module
        self.assertIs(kernel_module.ProcessFactory, runtime_module.SpawnFactory)

    def test_runner_kernel_and_verifier_share_runtime(self):
        runner = AgentRunner(MagicMock())
        kernel = VerificationKernel()
        verifier = Verifier(())
        self.assertIsInstance(runner._runtime, ProcessRuntime)
        self.assertIsInstance(kernel._runtime, ProcessRuntime)
        self.assertIsInstance(verifier._runtime, ProcessRuntime)

    def test_legacy_verifier_timeout_and_cancel_share_runtime(self):
        async def factory(*command, **kwargs):
            return HangingProcess()

        with tempfile.TemporaryDirectory() as directory:
            timeout_verifier = Verifier(
                (("slow",),), timeout_seconds=0.05,
                runtime=ProcessRuntime(spawn=factory),
            )
            timeout_checks = asyncio.run(timeout_verifier.run(directory))
            self.assertTrue(timeout_checks[0].timed_out)
            self.assertIsNone(timeout_checks[0].exit_code)

            cancel_event = threading.Event()
            cancel_event.set()
            cancel_verifier = Verifier(
                (("slow",),), timeout_seconds=30,
                runtime=ProcessRuntime(spawn=factory),
            )
            with self.assertRaises(OperationCancelled):
                asyncio.run(cancel_verifier.run(directory, cancel_event=cancel_event))

    def test_real_agent_and_verification_share_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AgentConfig("python", sys.executable, ("-c", "print('runtime-ok')"))
            agent = DetectedAgent(config, True, sys.executable)
            runner = AgentRunner(MagicMock())
            agent_result = asyncio.run(runner.run_agent(agent, "unused", root, timeout_seconds=30))
            self.assertTrue(agent_result.succeeded)
            self.assertIn("runtime-ok", agent_result.stdout)

            verifier = Verifier(
                ((sys.executable, "-c", "print('verify-ok')"),),
                timeout_seconds=30,
            )
            checks = asyncio.run(verifier.run(root))
            self.assertEqual(len(checks), 1)
            self.assertTrue(checks[0].succeeded)
            self.assertIn("verify-ok", checks[0].output)


if __name__ == "__main__":
    unittest.main()
