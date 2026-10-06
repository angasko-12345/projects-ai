# AgentOps Windows Packaging — Handoff (2026-10-06)

READ THIS FIRST. You are taking over an interrupted task: make AgentOps launchable
as a normal Windows desktop app (no terminal), verify the frozen EXE, fix genuine
defects, run tests, then STOP and report. DO NOT COMMIT OR PUSH. DO NOT `git add -A`.
DO NOT touch `ai-token-tracker`. Recovery backup (do NOT restore unless needed):
`D:\admin\recovery-2026-10-06\agentops`.

Repo root: `D:\admin\code\projects` (product dir: `agentops/`).
Test command: `cd agentops && python -m unittest discover -s tests`.
Env: Windows 10/11, Git Bash, Python 3.14.7, PySide6 6.11.2, PyInstaller 6.22.3.

---

## 1. Task state (what is DONE)

- Full architecture read and understood. `agentops_gui.py` (PyInstaller entry) →
  `agentops/gui/launcher.py::run()` (stdio/logging/error-box bootstrap) →
  `gui.main()` → `app.run()` (PySide6 QApplication).
- Fresh EXEs were rebuilt (the pre-reboot `dist/AgentOps.exe` was corrupt):
  `python scripts/build_windows_exe.py` (windowed) and PyInstaller with
  `AgentOpsDebug.spec` (console). Both build cleanly; windowed bootloader = `runw.exe`.
- Tests: **645 pass, 4 skipped** (`python -m unittest discover -s tests`).
  The 21 new tests in `tests/test_bundle_paths.py` + `tests/test_desktop_launcher.py`
  all pass (resource_path/_MEIPASS resolution, launcher bootstrap, PE subsystem=2).
- Archive verified with `pyi-archive_viewer dist/AgentOps.exe`: contains
  `agentops_gui`, PySide6, `qwindows.dll`, `agents/agents.yaml`, `chevron-down.svg`.
- Code-level path handling is correct: `agentops/paths.py::resource_path()` uses
  `sys._MEIPASS` when frozen; `config.py`, `gui/tokens.py`, `gui/settings.py`
  all use it; settings/logs go to `%APPDATA%/AgentOps/`.

## 2. THE BLOCKER — root cause isolated (this is the key finding)

The PyInstaller **onefile bootloader** fails with:

    [PYI-xxxx:ERROR] Could not create temporary directory!   (exit code -1 / 4294967295)

…but **ONLY when the EXE is launched from `D:\admin\code\projects\agentops\dist\`**.

PROOF (all with the same minimal one-file EXE, `scripts/onefile_spec.spec` →
`dist/test_onefile.exe`, which prints "hello from pyi"):

| EXE location | Result |
|---|---|
| `D:\admin\code\projects\agentops\dist\` | **FAILS** — bootloader error, exit -1, <1s |
| `D:\t\` (short path, root of D:) | WORKS — "hello from pyi", Python runs |
| `C:\tmp\exetest\` | WORKS — "hello from pyi", Python runs |
| `C:\tmp\deep\nested\path\for\exe\` | WORKS |

Also verified:
- Same failure for: minimal console one-file, minimal windowed one-file,
  one-file with a data file, one-file with PySide6 import, the real
  `AgentOps.exe`, and `AgentOpsDebug.exe`. So it is NOT bundle-specific.
- NOT the bootloader/PyInstaller version (minimal builds work from other paths).
- NOT TMP/TEMP: `GetTempPathW` → `C:\Users\admin\AppData\Local\Temp\`,
  directory exists and is writable (manual write test OK). Explicit TMP/TEMP
  env overrides did not help.
- NOT disk space (22 GB free on C:), NOT drive type (C: and D: both FIXED),
  NOT symlinks/junctions (`dir /al` empty, `os.path.islink` False).
- `AgentOps.exe` from `dist/` stays alive (poll None after 8s) but creates **no
  `_MEI*` dir in %TEMP% and writes no log** — the bootloader hangs/fails before
  extraction. The small console EXEs fail instantly with the error above.

So the trigger is something about the specific directory
`D:\admin\code\projects\agentops\dist` (path components, ACL, or how the sandbox
maps it). The old EXE was ALSO corrupt (rebuild fixed that), but the rebuild alone
did not fix launching from `dist/` because of the above.

## 3. Next steps (in order)

1. **Bisect the failing path.** Copy `dist/test_onefile.exe` into `D:\admin`,
   `D:\admin\code`, `D:\admin\code\projects`, `D:\admin\code\projects\agentops`,
   and run from each. Find the exact directory where it starts failing. Check
   `icacls` and `attrib` on the failing directory vs `D:\t`.
2. **Test an Explorer-style launch** (what a real user does):
   `cmd //c start "" "D:\admin\code\projects\agentops\dist\test_onefile.exe"`
   and the same for `AgentOps.exe`. If it works via Explorer/`start` but not via
   bash/Python-subprocess, the app is actually fine and the smoke test's launch
   method is the artifact — adjust `scripts/smoke_windows_exe.py` accordingly.
3. If the EXE works from a neutral path (e.g. copy `dist/` to `C:\AgentOps\` and
   double-click), conclude the bundle is correct and document the location quirk;
   run the smoke test against a copy in a working location.
4. Re-run the full test suite after any change:
   `cd agentops && python -m unittest discover -s tests` (expect 645 pass, 4 skip).
5. Produce the **AgentOps Recovery Report** (existing work / defects / verification /
   EXE verification / files to commit / build artifacts / verdict) and STOP.

## 4. Files

**Belong to the task (commit candidates — review first):**
- Modified: `agentops/AgentOps.spec`, `agentops/agentops/config.py`,
  `agentops/agentops/gui/tokens.py`, `agentops/agentops_gui.py`,
  `agentops/scripts/build_windows_exe.py`
- New: `agentops/AgentOpsDebug.spec`, `agentops/agentops/gui/launcher.py`,
  `agentops/agentops/paths.py`, `agentops/scripts/smoke_windows_exe.py`,
  `agentops/tests/test_bundle_paths.py`, `agentops/tests/test_desktop_launcher.py`

**Must stay untracked (gitignored or scratch):** `build/`, `dist/`, `build-debug/`,
`dist-debug/` (all gitignored). Scratch/debug files in `agentops/scripts/` created
during debugging — do NOT commit: `_run_smoke.bat`, `_env_check.bat`,
`_test_simple.bat`, `_pyinstaller_test.py`, `debug_exe.py`, `debug_exe2.py`,
`debug_exe3.py`, `test_pyinstaller.py`, `test_pyinstaller.spec`, `test_win.py`,
`test_data.py`, `test_pyside.py`, `onefile_spec.spec`, `windowed_spec.spec`,
`data_spec.spec`, `pyside_spec.spec`, `drivetype.py`, `islink.py`, and
`agentops/test_pyinstaller.spec`. Consider deleting them before finishing.

## 5. Acceptance criteria (from the original task)

1. AgentOps launches as a normal Windows desktop app by opening the EXE.
2. No terminal window for normal GUI use (PE subsystem=2 — verified).
3. Packaged resources resolve correctly (tests pass).
4. Desktop launcher works from the packaged EXE (blocked by §2).
5. EXE build reproducible per existing design (build script works).
6. Tests cover launcher/resource behavior (done).
7. Packaged EXE actually smoke-tested (blocked by §2).
8. No regressions (test suite green).
