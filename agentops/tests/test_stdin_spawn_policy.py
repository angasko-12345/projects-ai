"""Regression tests for the stdin spawn policy.

Issue #4 root cause: children spawned by ``ProcessRuntime`` inherited the
orchestrator's stdin.  A non-TTY agent that reads stdin to EOF (for example
``opencode run "<prompt>"``, anomalyco/opencode#52938) then blocks forever and
hits the 900 s timeout with zero output.  All spawned children must get
``stdin=DEVNULL`` unless a caller explicitly overrides it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

from agentops.runtime import ProcessRuntime


class _StubProcess:
    """Minimal process shape for injected-factory run_process calls."""

    def __init__(self):
        self.returncode = 0

    async def communicate(self):
        return b"", b""


class StdinSpawnPolicyTests(unittest.TestCase):
    def test_spawn_factory_receives_devnull_stdin_by_default(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(kwargs)
            return _StubProcess()

        asyncio.run(ProcessRuntime(spawn=factory).spawn_process("prog"))
        self.assertEqual(calls[0]["stdin"], asyncio.subprocess.DEVNULL)

    def test_spawn_process_stdin_can_be_overridden(self):
        calls = []
        sentinel = object()

        async def factory(*command, **kwargs):
            calls.append(kwargs)
            return _StubProcess()

        asyncio.run(
            ProcessRuntime(spawn=factory).spawn_process(
                "prog",
                stdin=sentinel,
            )
        )
        self.assertIs(calls[0]["stdin"], sentinel)

    def test_run_process_uses_devnull_stdin_by_default(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(kwargs)
            return _StubProcess()

        result = asyncio.run(
            ProcessRuntime(spawn=factory).run_process(
                ("prog",),
                cwd=".",
                env={},
                timeout=5,
            )
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(calls[0]["stdin"], asyncio.subprocess.DEVNULL)

    def test_run_process_stdin_can_be_overridden(self):
        calls = []
        sentinel = object()

        async def factory(*command, **kwargs):
            calls.append(kwargs)
            return _StubProcess()

        asyncio.run(
            ProcessRuntime(spawn=factory).run_process(
                ("prog",),
                cwd=".",
                env={},
                timeout=5,
                stdin=sentinel,
            )
        )
        self.assertIs(calls[0]["stdin"], sentinel)


class StdinInheritanceRegressionTests(unittest.TestCase):
    def test_run_process_does_not_inherit_test_process_stdin(self):
        """A child that reads stdin must not block on the parent's open pipe.

        The test replaces its own fd 0 with the read end of a pipe whose write
        end stays open for the duration of the child run, so no EOF can ever
        reach the child through an inherited stdin.
        """
        read_fd, write_fd = os.pipe()
        saved_stdin = os.dup(0)
        os.dup2(read_fd, 0)
        os.close(read_fd)
        try:
            result = asyncio.run(
                ProcessRuntime().run_process(
                    [sys.executable, "-c", "import sys; sys.stdin.read(); print('OK')"],
                    cwd=".",
                    timeout=5.0,
                )
            )
        finally:
            os.dup2(saved_stdin, 0)
            os.close(saved_stdin)
            os.close(write_fd)
        self.assertFalse(
            result.timed_out,
            f"child blocked on inherited stdin: stderr={result.stderr!r}",
        )
        self.assertFalse(result.cancelled)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), b"OK")


if __name__ == "__main__":
    unittest.main()
