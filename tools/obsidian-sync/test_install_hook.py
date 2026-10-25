"""Regression tests for install-hook.ps1.

Run: python -m unittest discover -s tools/obsidian-sync
The behavioural test needs `pwsh` (or Windows `powershell`) on PATH and skips
otherwise; it is not wired into CI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("install-hook.ps1")


class InstallHookTests(unittest.TestCase):
    def test_no_powershell_6_only_encoding_name(self):
        # Regression: `Set-Content -Encoding utf8NoBOM` is PowerShell 6+ only;
        # Windows PowerShell 5.1 (powershell.exe) rejects it and the install
        # aborts under $ErrorActionPreference = "Stop".
        self.assertIsNone(re.search(r"-Encoding\s+utf8NoBOM",
                                    SCRIPT.read_text(encoding="utf-8"),
                                    re.IGNORECASE))

    def test_hook_is_utf8_without_bom_with_lf_endings(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            self.skipTest("no PowerShell on PATH")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools" / "obsidian-sync").mkdir(parents=True)
            (root / ".git").mkdir()
            copy = root / "tools" / "obsidian-sync" / "install-hook.ps1"
            shutil.copyfile(SCRIPT, copy)
            completed = subprocess.run(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(copy)], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            hook = root / ".git" / "hooks" / "post-commit"
            data = hook.read_bytes()
            self.assertTrue(data.startswith(b"#!/bin/sh\n"), data[:16])
            self.assertNotIn(b"\r", data)
            self.assertTrue(data.endswith(b"exit 0\n"), data[-16:])
            if os.name != "nt":
                self.assertTrue(os.access(hook, os.X_OK))


if __name__ == "__main__":
    unittest.main()
