"""Shared ProcessRuntime contract tests (Track A3)."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import suppress
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


#: Real parent/child/grandchild roles for the Windows process-tree test.
#: Each role atomically publishes its own PID on entry so the test can
#: enumerate everything it started even when a later step fails; the chain
#: only reports "ready" once the grandchild has proven it is running.
_TREE_HELPER_SCRIPT = '''\
"""Parent/child/grandchild roles for the real process-tree termination test."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DIRECTORY = Path(sys.argv[2])
ROLE = sys.argv[1]


def publish(name, text):
    tmp = DIRECTORY / (name + ".tmp")
    tmp.write_text(str(text), encoding="utf-8")
    os.replace(tmp, DIRECTORY / name)


def spin():
    while True:
        time.sleep(0.25)


publish(ROLE + ".pid", os.getpid())

if ROLE == "grandchild":
    spin()

if ROLE == "child":
    grandchild = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "grandchild", str(DIRECTORY)])
    deadline = time.monotonic() + 30
    while not (DIRECTORY / "grandchild.pid").is_file():
        if grandchild.poll() is not None or time.monotonic() > deadline:
            raise SystemExit("grandchild never reported its pid")
        time.sleep(0.05)
    publish("ready.json",
            json.dumps({"child": os.getpid(), "grandchild": grandchild.pid}))
    spin()

if ROLE == "parent":
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "child", str(DIRECTORY)])
    deadline = time.monotonic() + 30
    while not (DIRECTORY / "ready.json").is_file():
        if child.poll() is not None or time.monotonic() > deadline:
            raise SystemExit("child chain never became ready")
        time.sleep(0.05)
    spin()
'''


def _recorded_pids(directory: Path, parent_pid: int) -> set[int]:
    """Every PID the tree scenario may have started, from handles and files."""
    pids = {parent_pid}
    for name in ("parent.pid", "child.pid", "grandchild.pid"):
        try:
            pids.add(int((directory / name).read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            pass
    try:
        ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        pids.add(int(ready["child"]))
        pids.add(int(ready["grandchild"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    pids.discard(0)
    return pids


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

        def fake_killpg(pid, sig):
            # killpg() SIGKILLs the whole group, so the child dies without
            # Popen.kill() ever being called. The double must model that or
            # communicate() hangs until the cleanup timeout instead of
            # returning the captured output.
            process.kill()

        with patch("os.killpg", create=True) as killpg:
            killpg.side_effect = fake_killpg
            stdout, stderr = asyncio.run(ProcessRuntime().terminate_process(process))
        killpg.assert_called_once()
        called_pid, called_signal = killpg.call_args.args
        self.assertEqual(called_pid, 4242)
        import signal as stdlib_signal
        self.assertEqual(called_signal, stdlib_signal.SIGKILL)
        self.assertTrue(process.killed)
        self.assertEqual((stdout, stderr), (b"partial", b""))

    def test_cleanup_timeout_is_bounded_when_the_process_never_dies(self):
        """The fix above must not weaken the bounded-cleanup guarantee."""
        class UnkillableProcess(HangingProcess):
            def kill(self):
                pass

        process = UnkillableProcess(pid=4242)
        with patch("os.killpg", create=True) as killpg:
            stdout, stderr = asyncio.run(ProcessRuntime().terminate_process(
                process, platform="posix", cleanup_timeout=0.05,
            ))
        killpg.assert_called_once()
        self.assertEqual((stdout, stderr),
                         (b"", b"Process did not exit within the cleanup timeout."))

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
            runner = AgentRunner(MagicMock(), systemd_run_enabled=False)
            agent_result = asyncio.run(runner.run_agent(agent, "unused", root, timeout_seconds=30))
            self.assertTrue(agent_result.succeeded)
            self.assertIn("runtime-ok", agent_result.stdout)

            verifier = Verifier(
                ((sys.executable, "-c", "print('verify-ok')"),),
                timeout_seconds=30,
                systemd_run_enabled=False,
            )
            checks = asyncio.run(verifier.run(root))
            self.assertEqual(len(checks), 1)
            self.assertTrue(checks[0].succeeded)
            self.assertIn("verify-ok", checks[0].output)

    @unittest.skipIf(sys.platform != "win32", "Windows process-tree termination")
    def test_real_parent_terminate_process_kills_descendant_tree(self):
        """ROOT-034 DBG-08: the real taskkill /T path must reach grandchildren.

        No fakes anywhere on the assertion path: a real parent spawned by
        ProcessRuntime spawns a real child, which spawns a real grandchild;
        only the parent is handed to terminate_process(), and the child and
        grandchild PIDs must disappear with it.  Deterministic sync (atomic
        PID/ready files) proves liveness before termination; bounded polling
        proves death afterwards.
        """
        import ctypes

        process_query_limited_information = 0x1000
        process_terminate = 0x0001
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p

        def alive(pid: int) -> bool:
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == still_active
            finally:
                kernel32.CloseHandle(handle)

        def force_kill(pid: int) -> None:
            handle = kernel32.OpenProcess(process_terminate, False, pid)
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)

        with tempfile.TemporaryDirectory() as directory:
            sync = Path(directory)
            helper = sync / "tree_helper.py"
            helper.write_text(_TREE_HELPER_SCRIPT, encoding="utf-8")
            runtime = ProcessRuntime()

            async def scenario():
                parent = await runtime.spawn_process(
                    sys.executable, str(helper), "parent", str(sync),
                )
                try:
                    ready = sync / "ready.json"
                    deadline = time.monotonic() + 30
                    while not ready.is_file():
                        if parent.returncode is not None:
                            self.fail(f"parent exited early ({parent.returncode})")
                        if time.monotonic() > deadline:
                            self.fail("descendant chain never became ready")
                        await asyncio.sleep(0.05)
                    pids = json.loads(ready.read_text(encoding="utf-8"))
                    child_pid, grandchild_pid = pids["child"], pids["grandchild"]
                    # Sync says started; these probes say demonstrably alive
                    # at the moment termination is about to be requested.
                    self.assertTrue(alive(child_pid), "child died before termination")
                    self.assertTrue(alive(grandchild_pid),
                                    "grandchild died before termination")

                    await runtime.terminate_process(parent)

                    deadline = time.monotonic() + 10
                    while ((alive(child_pid) or alive(grandchild_pid))
                           and time.monotonic() < deadline):
                        await asyncio.sleep(0.05)
                    self.assertFalse(alive(child_pid),
                                     "child survived terminate_process()")
                    self.assertFalse(
                        alive(grandchild_pid),
                        "grandchild survived terminate_process() — real tree "
                        "termination regressed",
                    )
                    self.assertIsNotNone(parent.returncode, "parent was not reaped")
                finally:
                    # Whatever assertion failed, nothing started here outlives it.
                    if parent.returncode is None:
                        with suppress(Exception):
                            await runtime.terminate_process(parent, cleanup_timeout=5)
                        with suppress(Exception):
                            await asyncio.wait_for(parent.communicate(), timeout=5)
                    for pid in _recorded_pids(sync, parent.pid):
                        if alive(pid):
                            force_kill(pid)

            asyncio.run(scenario())

    def test_systemd_isolation_is_opt_in(self):
        self.assertFalse(ProcessRuntime().systemd_run_enabled)

    def test_injected_factory_never_gets_systemd_prefix_or_probe(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            return FakeProcess()

        runtime = ProcessRuntime(spawn=factory, systemd_run_enabled=True)
        with patch.object(ProcessRuntime, "_probe_systemd_run",
                          side_effect=AssertionError("probe must not run")), \
                patch("agentops.runtime.sys", platform="linux"):
            asyncio.run(runtime.spawn_process("prog", cwd=".", env={}))
        self.assertEqual(calls, [("prog",)])

    def test_enabled_isolation_wraps_owned_spawns_on_linux(self):
        captured = []

        async def fake_exec(*command, **kwargs):
            captured.append(command)
            return FakeProcess()

        runtime = ProcessRuntime(systemd_run_enabled=True)
        with patch.object(ProcessRuntime, "_probe_systemd_run", return_value=True), \
                patch("agentops.runtime.sys", platform="linux"), \
                patch("agentops.runtime.asyncio.create_subprocess_exec", fake_exec):
            asyncio.run(runtime.spawn_process("prog", cwd=".", env={}))
        self.assertEqual(captured[0][:3], ("systemd-run", "--user", "--scope"))
        self.assertEqual(captured[0][-1], "prog")

    def test_enabled_isolation_is_ignored_off_linux(self):
        captured = []

        async def fake_exec(*command, **kwargs):
            captured.append(command)
            return FakeProcess()

        runtime = ProcessRuntime(systemd_run_enabled=True)
        for platform in ("win32", "darwin"):
            with patch.object(ProcessRuntime, "_probe_systemd_run",
                              side_effect=AssertionError("probe must not run")), \
                    patch("agentops.runtime.sys", platform=platform), \
                    patch("agentops.runtime.asyncio.create_subprocess_exec", fake_exec):
                asyncio.run(runtime.spawn_process("prog", cwd=".", env={}))
        self.assertEqual(captured, [("prog",), ("prog",)])

    def test_enabled_isolation_fails_fast_when_unavailable(self):
        runtime = ProcessRuntime(systemd_run_enabled=True)
        with patch.object(ProcessRuntime, "_probe_systemd_run", return_value=False), \
                patch("agentops.runtime.sys", platform="linux"):
            with self.assertRaises(RuntimeError):
                asyncio.run(runtime.spawn_process("prog", cwd=".", env={}))


if __name__ == "__main__":
    unittest.main()
