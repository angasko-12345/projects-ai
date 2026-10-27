"""Real cmd.exe checks for safe prompt delivery (upgrade U1). Windows only.

These spawn genuine ``.cmd`` shims through AgentRunner, so they prove (or
disprove) on a Windows host what ``test_prompt_transport.py`` simulates on
Linux: a multi-line prompt containing ``"a & b | c > d"`` reaches the agent
byte-exact, or the run is refused before anything is spawned.  The fixture
agent is generated in a temp directory per test (no committed batch files, so
no CRLF/gitattributes surprises).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentops.config import AgentConfig
from agentops.logging import LogManager
from agentops.prompt_transport import PromptTransportError
from agentops.registry import DetectedAgent
from agentops.runner import AgentRunner

PROMPT = 'You are the implementation agent.\nTask: "a & b | c > d"\nReturn JSON {"ok": true}'

RECORDER_PY = r"""
import json, sys
record = sys.argv[1]
payload = {"argv": sys.argv[2:]}
if "--read-stdin" in sys.argv[2:]:
    payload["stdin"] = sys.stdin.buffer.read().decode("utf-8")
if "--read-file" in sys.argv[2:]:
    with open(sys.argv[-1], "rb") as handle:
        payload["file"] = handle.read().decode("utf-8")
with open(record, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
"""

RECORDER_JS = r"""
const fs = require("fs");
const [record, ...rest] = process.argv.slice(2);
fs.writeFileSync(record, JSON.stringify({ argv: rest }), "utf8");
"""


@unittest.skipUnless(sys.platform == "win32", "requires cmd.exe")
class WindowsCmdShimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.record = self.root / "record.json"
        (self.root / "recorder.py").write_text(RECORDER_PY, encoding="utf-8")
        # A plain (non-npm) batch wrapper: unresolvable, so only stdin/file
        # transports or a refusal can be correct for multi-line prompts.
        self.shim = self.root / "fakeagent.cmd"
        self.shim.write_bytes(
            f'@echo off\r\n"{sys.executable}" "{self.root / "recorder.py"}" "{self.record}" %*\r\n'.encode(
                "ascii"
            )
        )
        self.logs = LogManager(self.root / "logs")

    def _run(self, config: AgentConfig, executable: Path):
        agent = DetectedAgent(config, True, str(executable))
        return asyncio.run(AgentRunner(self.logs).run_agent(agent, PROMPT, str(self.root), "t"))

    def _recorded(self) -> dict:
        return json.loads(self.record.read_text(encoding="utf-8"))

    def test_premise_raw_cmd_shim_truncates_a_multiline_argument(self):
        # Documents the bug U1 fixes. If this ever fails, cmd.exe stopped
        # truncating and the refusal path is merely conservative.
        prompt = "line one\nline two"
        subprocess.run(
            [str(self.shim), prompt],
            cwd=str(self.root),
            timeout=60,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        if self.record.exists():
            self.assertNotEqual(self._recorded()["argv"], [prompt])

    def test_auto_refuses_multiline_prompt_on_unresolvable_shim(self):
        with self.assertRaises(PromptTransportError):
            self._run(AgentConfig("fake", "fakeagent", ("run", "{prompt}")), self.shim)
        self.assertFalse(self.record.exists(), "nothing may be spawned on refusal")

    def test_stdin_transport_through_cmd_shim_is_byte_exact(self):
        result = self._run(
            AgentConfig("fake", "fakeagent", ("run", "{prompt}"), stdin_args=("--read-stdin",)),
            self.shim,
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self._recorded()["stdin"], PROMPT)
        self.assertEqual(self._recorded()["argv"], ["--read-stdin"])

    def test_file_transport_through_cmd_shim_is_byte_exact(self):
        result = self._run(
            AgentConfig(
                "fake", "fakeagent", ("--read-file", "{prompt_file}"), prompt_transport="file"
            ),
            self.shim,
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self._recorded()["file"], PROMPT)

    def test_npm_style_shim_resolves_to_node_entry_and_argv_is_byte_exact(self):
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        package = self.root / "npm" / "node_modules" / "fake-agent" / "bin"
        package.mkdir(parents=True)
        (package / "fake.js").write_text(RECORDER_JS, encoding="utf-8")
        shim = self.root / "npm" / "fake.cmd"
        shim.write_bytes(
            (
                "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\n"
                "SETLOCAL\r\nCALL :find_dp0\r\n\r\n"
                'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n'
                '  SET "_prog=node"\r\n  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n'
                'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
                '"%dp0%\\node_modules\\fake-agent\\bin\\fake.js" %*\r\n'
            ).encode("ascii")
        )
        result = self._run(AgentConfig("fake", "fake", (str(self.record), "run", "{prompt}")), shim)
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self._recorded()["argv"], ["run", PROMPT])


if __name__ == "__main__":
    unittest.main()
