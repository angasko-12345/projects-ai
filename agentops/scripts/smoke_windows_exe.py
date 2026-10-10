"""Launch the built ``AgentOps.exe`` and verify it works as a desktop application.

Usage from the ``agentops/`` product directory::

    python scripts/smoke_windows_exe.py [--exe dist/AgentOps.exe] [--timeout 90]

A successful PyInstaller build is not evidence of a working bundle. This script
double-click-equivalents the executable and asserts that it starts with no
Python on ``PATH`` and no ``PYTHONHOME``/``PYTHONPATH``, from an unrelated
working directory, with an isolated ``APPDATA`` so the developer's own settings
are never touched; that a real ``AgentOps`` main window appears; that the
launcher wrote its log; that the working directory stays free of generated
state; and that closing the window exits the process cleanly.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = ROOT / "dist" / "AgentOps.exe"
WINDOW_TITLE = "AgentOps"
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_IS_WINDOWS = sys.platform == "win32"
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Handles are pointers: without explicit prototypes ctypes truncates them to
# 32 bits on x64 and the wrong window (or none) gets matched.
user32 = ctypes.WinDLL("user32", use_last_error=True) if _IS_WINDOWS else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if _IS_WINDOWS else None
if _IS_WINDOWS:
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def window_image_name(hwnd: int) -> str:
    """File name of the executable owning ``hwnd``, or an empty string."""
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(512)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_main_window(title: str, executable: str) -> int | None:
    """Return the handle of a visible top-level window of ``executable``, else ``None``."""
    found: list[int] = []

    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title and window_image_name(hwnd) == executable:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(collect), 0)
    return found[0] if found else None


def python_free_environment(appdata: Path) -> dict[str, str]:
    """A copy of the environment with no Python reachable and an isolated APPDATA."""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONHOME", "PYTHONPATH")}
    kept = [
        entry for entry in env.get("PATH", "").split(os.pathsep)
        if not (Path(entry or ".") / "python.exe").exists()
    ]
    env["PATH"] = os.pathsep.join(kept)
    env["APPDATA"] = str(appdata)
    return env


def wait_for_window(executable: str, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handle = find_main_window(WINDOW_TITLE, executable)
        if handle:
            return handle
        time.sleep(0.5)
    return None


def read_log(appdata: Path) -> str:
    log = appdata / "AgentOps" / "logs" / "agentops.log"
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def terminate(process: subprocess.Popen) -> None:
    """Never leave a stranded AgentOps.exe behind."""
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True,
        check=False,
    )
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def smoke(exe: Path, timeout: float) -> int:
    if not exe.exists():
        print(f"ERROR: executable not found: {exe}")
        print("       Build it first: python scripts/build_windows_exe.py")
        return 1
    if not _IS_WINDOWS:
        print("ERROR: this smoke test exercises the Windows executable and needs Windows.")
        return 1

    with tempfile.TemporaryDirectory(prefix="agentops-smoke-") as workspace:
        workdir = Path(workspace) / "arbitrary-cwd"
        appdata = Path(workspace) / "appdata"
        workdir.mkdir()
        appdata.mkdir()
        env = python_free_environment(appdata)

        print(f"Executable  {exe} ({exe.stat().st_size:,} bytes)")
        print(f"Working dir {workdir}")
        print(f"APPDATA     {appdata} (isolated from the developer's own settings)")
        print(f"PATH        {len(os.pathsep.split(env['PATH']))} entries, none containing python.exe")

        started = time.monotonic()
        process = subprocess.Popen([str(exe)], cwd=str(workdir), env=env)
        try:
            handle = wait_for_window(exe.name, timeout)
            elapsed = time.monotonic() - started
            log_text = read_log(appdata)

            if handle is None:
                print(f"FAIL: no visible '{WINDOW_TITLE}' window within {timeout:.0f}s.")
                print("--- agentops.log ---")
                print(log_text or "(no log written)")
                return 1
            print(f"PASS: window '{WINDOW_TITLE}' visible after {elapsed:.1f}s (hwnd={handle})")

            if "Traceback" in log_text:
                print("FAIL: launcher log contains a traceback.")
                print(log_text)
                return 1
            if "starting" not in log_text:
                print("FAIL: launcher log is missing its startup line.")
                print(log_text or "(no log written)")
                return 1
            print(f"PASS: launcher log written ({len(log_text.strip().splitlines())} line(s))")

            user32.PostMessageW(handle, WM_CLOSE, 0, 0)
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                print("FAIL: process did not exit within 30s of closing the window.")
                return 1
            if code != 0:
                print(f"FAIL: process exited with code {code}.")
                return 1
            print("PASS: closed the window and the process exited with code 0")

            leftover = sorted(item.name for item in workdir.iterdir())
            if leftover:
                print(f"FAIL: executable wrote into its working directory: {leftover}")
                return 1
            print("PASS: working directory stayed empty; resources resolved from the bundle")
            print("OK: AgentOps.exe launches the GUI from an arbitrary directory.")
            return 0
        finally:
            terminate(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch AgentOps.exe and verify the GUI starts.")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE, help="executable to launch")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the window")
    args = parser.parse_args(argv)
    return smoke(args.exe.resolve(), args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())