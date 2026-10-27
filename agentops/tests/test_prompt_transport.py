"""Regression tests for safe prompt delivery (upgrade U1).

On Windows an npm-installed agent CLI usually resolves to a ``.cmd`` shim, and
cmd.exe ends the forwarded command line at the first newline, so a multi-line
prompt passed as argv arrives truncated (multica-ai/multica#1718).  These
tests drive the transport decision through injected platform / filesystem
seams so the whole Windows matrix runs on Linux; the real-``cmd.exe`` checks
live in ``test_prompt_transport_windows.py`` and run only on Windows.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock

from agentops.agent_run import AgentRunContext
from agentops.config import AgentConfig, load_config
from agentops.failure import FailureCategory, FailureClassifier
from agentops.logging import LogManager
from agentops.prompt_transport import (
    PromptTransportError,
    executable_kind,
    is_cmd_safe,
    prepare_prompt_delivery,
    resolve_npm_shim,
    resolve_opencode_native,
)
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner
from agentops.runtime import ProcessRuntime

MULTILINE = (
    'You are the implementation agent.\nTask: "a & b | c > d" ^ 100%\nReturn JSON {"ok": true}'
)

NEW_STYLE_SHIM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
"""

OLD_STYLE_SHIM = r"""@IF EXIST "%~dp0\node.exe" (
  "%~dp0\node.exe"  "%~dp0\node_modules\kilo\bin\kilo" %*
) ELSE (
  @SETLOCAL
  @SET PATHEXT=%PATHEXT:;.JS;=;%
  node  "%~dp0\node_modules\kilo\bin\kilo" %*
)
"""

EXE_TARGET_SHIM = '@"%~dp0\\node_modules\\tool\\bin\\tool.exe"   %*\r\n'


def _fs(files: dict[str, str] | set[str]):
    """exists/read_text seams over an in-memory set of Windows paths."""
    contents = files if isinstance(files, dict) else {path: "" for path in files}
    lowered = {path.lower(): text for path, text in contents.items()}

    def exists(path: str) -> bool:
        return path.lower() in lowered

    def read_text(path: str) -> str:
        if path.lower() not in lowered:
            raise FileNotFoundError(path)
        return lowered[path.lower()]

    return exists, read_text


def _deliver(
    executable,
    prompt=MULTILINE,
    *,
    platform="win32",
    files=None,
    which=None,
    machine="AMD64",
    args=("run", "{prompt}"),
    name="agent",
    **kwargs,
):
    exists, read_text = _fs(files or {})
    return prepare_prompt_delivery(
        name=name,
        executable=executable,
        args=args,
        prompt=prompt,
        platform=platform,
        exists=exists,
        read_text=read_text,
        which=which or (lambda _name: None),
        machine=machine,
        **kwargs,
    )


class ExecutableKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(executable_kind(r"C:\npm\opencode.cmd"), "cmd")
        self.assertEqual(executable_kind(r"C:\npm\TOOL.BAT"), "cmd")
        self.assertEqual(executable_kind(r"C:\bin\claude.exe"), "exe")
        self.assertEqual(executable_kind("/usr/local/bin/opencode"), "exe")
        self.assertEqual(executable_kind(r"C:\npm\opencode.ps1"), "script")
        self.assertEqual(executable_kind("/home/u/.local/bin/tool.py"), "script")

    def test_cmd_safety(self):
        self.assertTrue(is_cmd_safe("plain words, (parens) and 'quotes'"))
        for character in '\r\n"&|<>^%':
            self.assertFalse(is_cmd_safe(f"a{character}b"), repr(character))


class SelectionMatrixTests(unittest.TestCase):
    def test_posix_keeps_argv_byte_identical(self):
        delivery = _deliver("/usr/bin/opencode", platform="linux")
        self.assertEqual(delivery.argv, ("/usr/bin/opencode", "run", MULTILINE))
        self.assertEqual(delivery.transport, "argv")
        self.assertIsNone(delivery.stdin_bytes)
        self.assertEqual(delivery.resolution, "direct")

    def test_posix_ignores_cmd_suffix(self):
        # A ".cmd" file on POSIX is not run by cmd.exe; nothing changes.
        delivery = _deliver("/opt/odd/agent.cmd", platform="linux")
        self.assertEqual(delivery.argv[-1], MULTILINE)

    def test_windows_native_exe_keeps_argv(self):
        delivery = _deliver(r"C:\Tools\claude.exe")
        self.assertEqual(delivery.argv, (r"C:\Tools\claude.exe", "run", MULTILINE))
        self.assertEqual(delivery.executable_kind, "exe")

    def test_windows_unresolvable_shim_with_multiline_prompt_is_refused(self):
        with self.assertRaises(PromptTransportError) as caught:
            _deliver(r"C:\npm\copilot.cmd", name="copilot")
        message = str(caught.exception)
        self.assertIn("'copilot'", message)
        self.assertIn(r"C:\npm\copilot.cmd", message)
        self.assertIn("agents.copilot.prompt_transport", message)
        self.assertNotIn("a & b", message)  # never echo the prompt

    def test_windows_shim_with_single_line_safe_prompt_passes_through(self):
        delivery = _deliver(r"C:\npm\copilot.cmd", prompt="fix the failing test in parser module")
        self.assertEqual(
            delivery.argv, (r"C:\npm\copilot.cmd", "run", "fix the failing test in parser module")
        )
        self.assertEqual(delivery.resolution, "batch_shim")

    def test_each_unsafe_character_alone_triggers_refusal(self):
        for character in '\r\n"&|<>^%':
            with self.subTest(character=character), self.assertRaises(PromptTransportError):
                _deliver(r"C:\npm\kilo.cmd", prompt=f"one{character}two")

    def test_batch_suffix_bat_is_treated_like_cmd(self):
        with self.assertRaises(PromptTransportError):
            _deliver(r"C:\tools\agent.bat")

    def test_windows_shim_uses_stdin_when_declared(self):
        delivery = _deliver(
            r"C:\npm\codex.cmd", args=("exec", "{prompt}"), stdin_args=("exec", "-")
        )
        self.assertEqual(delivery.transport, "stdin")
        self.assertEqual(delivery.argv, (r"C:\npm\codex.cmd", "exec", "-"))
        self.assertEqual(delivery.stdin_bytes, MULTILINE.encode("utf-8"))
        self.assertNotIn(MULTILINE, delivery.argv)

    def test_windows_shim_uses_prompt_file_when_declared(self):
        delivery = _deliver(
            r"C:\npm\pi.cmd",
            args=("--print", "{prompt}", "--file", "{prompt_file}"),
            prompt_file=r"C:\repo\.agentops\logs\prompts\x.prompt.txt",
        )
        self.assertEqual(delivery.transport, "file")
        self.assertEqual(delivery.argv[-1], r"C:\repo\.agentops\logs\prompts\x.prompt.txt")
        self.assertNotIn(MULTILINE, delivery.argv)

    def test_explicit_argv_on_unresolvable_shim_is_refused_even_with_stdin_declared(self):
        with self.assertRaises(PromptTransportError) as caught:
            _deliver(r"C:\npm\codex.cmd", transport="argv", stdin_args=("exec", "-"))
        self.assertIn('"argv"', str(caught.exception))

    def test_explicit_stdin_applies_on_every_platform(self):
        delivery = _deliver(
            "/usr/bin/codex",
            platform="linux",
            transport="stdin",
            args=("exec", "{prompt}"),
            stdin_args=("exec", "-"),
        )
        self.assertEqual(delivery.argv, ("/usr/bin/codex", "exec", "-"))
        self.assertEqual(delivery.stdin_bytes, MULTILINE.encode())

    def test_stdin_without_stdin_args_rejects_prompt_placeholder(self):
        with self.assertRaises(ValueError):
            _deliver("/usr/bin/x", platform="linux", transport="stdin", args=("{prompt}",))

    def test_auto_with_only_prompt_file_placeholder_uses_file_on_posix(self):
        delivery = _deliver(
            "/usr/bin/x",
            platform="linux",
            args=("--file", "{prompt_file}"),
            prompt_file="/tmp/p.txt",
        )
        self.assertEqual(delivery.argv, ("/usr/bin/x", "--file", "/tmp/p.txt"))
        self.assertEqual(delivery.transport, "file")

    def test_metadata_is_prompt_free(self):
        delivery = _deliver(r"C:\npm\codex.cmd", stdin_args=("-",))
        self.assertEqual(
            delivery.metadata(),
            {
                "prompt_transport": "stdin",
                "executable_kind": "cmd",
                "executable_resolution": "batch_shim",
            },
        )


class OpencodeNativeResolutionTests(unittest.TestCase):
    BASE = r"C:\Users\h\AppData\Roaming\npm"

    def _native(self, arch):
        return (
            rf"{self.BASE}\node_modules\opencode-ai\node_modules"
            rf"\opencode-windows-{arch}\bin\opencode.exe"
        )

    def test_x64_native_exe_bypasses_shim(self):
        delivery = _deliver(rf"{self.BASE}\opencode.cmd", files={self._native("x64")})
        self.assertEqual(delivery.argv, (self._native("x64"), "run", MULTILINE))
        self.assertEqual(delivery.resolution, "opencode_native_exe")
        self.assertEqual(delivery.executable_kind, "exe")
        self.assertEqual(delivery.transport, "argv")

    def test_x64_prefers_regular_over_baseline(self):
        found = resolve_opencode_native(
            rf"{self.BASE}\opencode.cmd",
            machine="AMD64",
            exists=_fs({self._native("x64"), self._native("x64-baseline")})[0],
        )
        self.assertEqual(found, self._native("x64"))

    def test_x64_falls_back_to_baseline(self):
        delivery = _deliver(rf"{self.BASE}\opencode.cmd", files={self._native("x64-baseline")})
        self.assertEqual(delivery.argv[0], self._native("x64-baseline"))

    def test_arm64_prefers_arm64_then_x64(self):
        files = {self._native("arm64"), self._native("x64")}
        self.assertEqual(
            _deliver(rf"{self.BASE}\opencode.cmd", files=files, machine="ARM64").argv[0],
            self._native("arm64"),
        )
        self.assertEqual(
            _deliver(
                rf"{self.BASE}\opencode.cmd", files={self._native("x64")}, machine="ARM64"
            ).argv[0],
            self._native("x64"),
        )

    def test_only_opencode_gets_the_opencode_layout(self):
        # kilo.cmd next to an opencode native binary must not be redirected.
        with self.assertRaises(PromptTransportError):
            _deliver(rf"{self.BASE}\kilo.cmd", files={self._native("x64")})


class NpmShimResolutionTests(unittest.TestCase):
    BASE = r"C:\npm"

    def test_new_style_shim_resolves_to_local_node_and_entry(self):
        entry = rf"{self.BASE}\node_modules\@openai\codex\bin\codex.js"
        node = rf"{self.BASE}\node.exe"
        files = {rf"{self.BASE}\codex.cmd": NEW_STYLE_SHIM, entry: "", node: ""}
        delivery = _deliver(rf"{self.BASE}\codex.cmd", files=files, args=("exec", "{prompt}"))
        self.assertEqual(delivery.argv, (node, entry, "exec", MULTILINE))
        self.assertEqual(delivery.resolution, "npm_shim_node_entry")
        self.assertEqual(delivery.transport, "argv")

    def test_old_style_shim_resolves_with_node_from_path(self):
        entry = rf"{self.BASE}\node_modules\kilo\bin\kilo"
        files = {rf"{self.BASE}\kilo.cmd": OLD_STYLE_SHIM, entry: ""}
        delivery = _deliver(
            rf"{self.BASE}\kilo.cmd",
            files=files,
            which=lambda name: r"C:\Program Files\nodejs\node.exe" if name == "node" else None,
        )
        self.assertEqual(delivery.argv[:2], (r"C:\Program Files\nodejs\node.exe", entry))
        self.assertEqual(delivery.argv[-1], MULTILINE)

    def test_shim_targeting_an_exe_runs_it_directly(self):
        target = rf"{self.BASE}\node_modules\tool\bin\tool.exe"
        files = {rf"{self.BASE}\tool.cmd": EXE_TARGET_SHIM, target: ""}
        delivery = _deliver(rf"{self.BASE}\tool.cmd", files=files)
        self.assertEqual(delivery.argv, (target, "run", MULTILINE))
        self.assertEqual(delivery.resolution, "npm_shim_exe")

    def test_node_that_is_itself_a_shim_is_not_used(self):
        entry = rf"{self.BASE}\node_modules\kilo\bin\kilo"
        files = {rf"{self.BASE}\kilo.cmd": OLD_STYLE_SHIM, entry: ""}
        self.assertIsNone(
            resolve_npm_shim(
                rf"{self.BASE}\kilo.cmd",
                exists=_fs(files)[0],
                read_text=_fs(files)[1],
                which=lambda _name: r"C:\volta\node.cmd",
            )
        )

    def test_missing_entry_script_is_not_resolved(self):
        files = {rf"{self.BASE}\codex.cmd": NEW_STYLE_SHIM, rf"{self.BASE}\node.exe": ""}
        with self.assertRaises(PromptTransportError):
            _deliver(rf"{self.BASE}\codex.cmd", files=files)

    def test_non_npm_batch_file_is_not_resolved(self):
        files = {rf"{self.BASE}\agent.cmd": "@echo off\r\npython C:\\x\\agent.py %*\r\n"}
        with self.assertRaises(PromptTransportError):
            _deliver(rf"{self.BASE}\agent.cmd", files=files)

    def test_unreadable_shim_is_not_resolved(self):
        self.assertIsNone(
            resolve_npm_shim(
                r"C:\npm\gone.cmd",
                exists=lambda _p: False,
                read_text=lambda _p: (_ for _ in ()).throw(OSError("denied")),
                which=lambda _n: None,
            )
        )


class RefusalClassificationTests(unittest.TestCase):
    def test_refusal_classifies_as_non_retryable_environment_failure(self):
        with self.assertRaises(PromptTransportError) as caught:
            _deliver(r"C:\npm\opencode.cmd", name="opencode")
        result = FailureClassifier.classify(error=str(caught.exception), source="AGENT")
        self.assertIs(result.category, FailureCategory.ENVIRONMENT_FAILURE)
        self.assertFalse(result.retryable)


class ConfigTests(unittest.TestCase):
    def _load(self, agent: dict) -> AgentConfig:
        data = {"agents": {"a": {"command": "a", **agent}}, "role_preferences": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agents.yaml"
            path.write_text(json.dumps(data), encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return load_config(path).agents["a"]

    def test_defaults_are_auto_and_no_stdin_support(self):
        agent = self._load({"args": ["run", "{prompt}"]})
        self.assertEqual(agent.prompt_transport, "auto")
        self.assertIsNone(agent.stdin_args)

    def test_stdin_args_parsed(self):
        agent = self._load({"args": ["exec", "{prompt}"], "stdin_args": ["exec", "-"]})
        self.assertEqual(agent.stdin_args, ("exec", "-"))

    def test_invalid_values_rejected(self):
        bad = [
            {"args": ["{prompt}"], "prompt_transport": "pipe"},
            {"args": ["{prompt}"], "stdin_args": "-"},
            {"args": ["{prompt}"], "stdin_args": ["{prompt}"]},
            {"args": ["{prompt}"], "prompt_transport": "file"},
            {"args": ["{prompt}", "{prompt_file}"], "prompt_transport": "file"},
            {"args": ["{prompt}"], "prompt_transport": "stdin"},
            {"args": ["--file", "{prompt_file}"], "prompt_transport": "argv"},
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self._load(raw)

    def test_bundled_config_still_loads_with_auto_everywhere(self):
        from agentops.config import load_config as bundled

        for agent in bundled().agents.values():
            self.assertEqual(agent.prompt_transport, "auto")


class _Process:
    def __init__(self):
        self.returncode = 0
        self.inputs: list[object] = []

    async def communicate(self, *args):
        self.inputs.append(args[0] if args else None)
        return b"done\n", b""


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = LogManager(Path(self.tmp.name) / "logs")

    def _runner(self, platform=None, factory=None, observer=None):
        runtime = ProcessRuntime(spawn=factory) if factory else None
        return AgentRunner(self.logs, runtime=runtime, platform=platform, run_observer=observer)

    def test_windows_unresolvable_shim_refuses_before_spawning(self):
        calls = []

        async def factory(*command, **kwargs):
            calls.append(command)
            return _Process()

        observer = MagicMock()
        observer.create_run.return_value = "run-1"
        agent = DetectedAgent(
            AgentConfig("copilot", "copilot", ("-p", "{prompt}")),
            True,
            str(Path(self.tmp.name) / "copilot.cmd"),
        )
        with self.assertRaises(PromptTransportError):
            asyncio.run(
                self._runner("win32", factory, observer).run_agent(
                    agent, MULTILINE, self.tmp.name, "task-1"
                )
            )
        self.assertEqual(calls, [])
        outcome = observer.finish_run.call_args.args[1]
        self.assertEqual(outcome.failure_classification, "environment")
        self.assertNotIn("a & b", outcome.error)

    def test_windows_stdin_transport_pipes_prompt_and_records_metadata(self):
        calls = []
        process = _Process()

        async def factory(*command, **kwargs):
            calls.append((command, kwargs))
            return process

        observer = MagicMock()
        observer.create_run.return_value = "run-1"
        shim = str(Path(self.tmp.name) / "codex.cmd")
        agent = DetectedAgent(
            AgentConfig("codex", "codex", ("exec", "{prompt}"), stdin_args=("exec", "-")),
            True,
            shim,
        )
        result = asyncio.run(
            self._runner("win32", factory, observer).run_agent(
                agent, MULTILINE, self.tmp.name, "task-1"
            )
        )
        command, kwargs = calls[0]
        self.assertEqual(command, (shim, "exec", "-"))
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.PIPE)
        self.assertEqual(process.inputs, [MULTILINE.encode("utf-8")])
        context: AgentRunContext = observer.create_run.call_args.args[0]
        self.assertEqual(context.command_metadata["prompt_transport"], "stdin")
        self.assertEqual(context.command_metadata["executable_kind"], "cmd")
        self.assertNotIn(MULTILINE, json.dumps(context.command_metadata))
        self.assertEqual(result.exit_code, 0)

    def test_argv_transport_keeps_devnull_stdin(self):
        calls = []
        process = _Process()

        async def factory(*command, **kwargs):
            calls.append(kwargs)
            return process

        agent = DetectedAgent(AgentConfig("x", "x", ("{prompt}",)), True, "/usr/bin/x")
        asyncio.run(self._runner("linux", factory).run_agent(agent, MULTILINE, self.tmp.name, "t"))
        self.assertEqual(calls[0]["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(process.inputs, [None])  # communicate() called without input

    def test_real_child_receives_stdin_prompt_byte_exact_and_sees_eof(self):
        script = "import sys; data = sys.stdin.buffer.read(); sys.stdout.write(data.hex())"
        agent = DetectedAgent(
            AgentConfig("py", sys.executable, ("-c", script), prompt_transport="stdin"),
            True,
            sys.executable,
        )
        result = asyncio.run(self._runner().run_agent(agent, MULTILINE, self.tmp.name, "t"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertFalse(result.timed_out)
        self.assertEqual(bytes.fromhex(result.stdout.strip()), MULTILINE.encode("utf-8"))

    def test_real_child_reads_prompt_file_which_is_deleted_afterwards(self):
        script = (
            "import sys, pathlib; p = pathlib.Path(sys.argv[1]); "
            "sys.stdout.write(p.read_bytes().hex() + ' ' + str(p))"
        )
        agent = DetectedAgent(
            AgentConfig(
                "py", sys.executable, ("-c", script, "{prompt_file}"), prompt_transport="file"
            ),
            True,
            sys.executable,
        )
        result = asyncio.run(self._runner().run_agent(agent, MULTILINE, self.tmp.name, "t"))
        self.assertEqual(result.exit_code, 0, result.stderr)
        payload, path = result.stdout.strip().split(" ", 1)
        self.assertEqual(bytes.fromhex(payload), MULTILINE.encode("utf-8"))
        self.assertTrue(path.startswith(str(self.logs.root)))
        self.assertFalse(Path(path).exists())

    def test_real_child_argv_unchanged_on_posix(self):
        if sys.platform == "win32":
            self.skipTest("POSIX argv behaviour")
        script = "import sys; sys.stdout.write(sys.argv[1].encode().hex())"
        agent = DetectedAgent(
            AgentConfig("py", sys.executable, ("-c", script, "{prompt}")), True, sys.executable
        )
        result = asyncio.run(self._runner().run_agent(agent, MULTILINE, self.tmp.name, "t"))
        self.assertEqual(bytes.fromhex(result.stdout.strip()), MULTILINE.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
