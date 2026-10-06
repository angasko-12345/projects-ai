"""Build the reproducible Windows AgentOps desktop executable.

Usage from the ``agentops/`` product directory::

    python -m pip install ".[windows,desktop]"
    python scripts/build_windows_exe.py [--smoke]

Writes ``agentops/dist/AgentOps.exe`` (gitignored; ship it as a release
asset). The build is working-directory independent: every path is resolved
from this file's own location. ``--smoke`` additionally launches the produced
executable and verifies the GUI actually comes up.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "AgentOps.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
EXE = DIST / "AgentOps.exe"
EXE_NAME = "AgentOps.exe"

# PE optional-header field we care about: a windowed build reports 2, a console
# build reports 3. Reading it proves "no console window" instead of assuming it.
PE_SUBSYSTEM_OFFSET = 68
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2


def pe_subsystem(exe: Path) -> int | None:
    """Return the PE subsystem of ``exe``, or ``None`` if it is not a PE file."""
    try:
        data = exe.read_bytes()
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    header = int.from_bytes(data[0x3C:0x40], "little")
    if data[header:header + 4] != b"PE\0\0":
        return None
    optional = header + 24
    if int.from_bytes(data[optional:optional + 2], "little") not in (0x10B, 0x20B):
        return None
    return int.from_bytes(
        data[optional + PE_SUBSYSTEM_OFFSET:optional + PE_SUBSYSTEM_OFFSET + 2], "little"
    )


def running_instances() -> bool:
    """True when AgentOps.exe is already running (its file would be locked)."""
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/NH", "/FO", "CSV"],
        capture_output=True,
        text=True,
        check=False,
    )
    return EXE_NAME in result.stdout


def build(skip_process_check: bool) -> int:
    if not SPEC.exists():
        print(f"ERROR: missing PyInstaller specification: {SPEC}")
        return 1
    if not skip_process_check and running_instances():
        print(f"ERROR: {EXE_NAME} is running; a locked executable cannot be rebuilt.")
        print("       Close it and re-run, or pass --skip-process-check.")
        return 1
    print(f"Building {SPEC}")
    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(BUILD),
            str(SPEC),
        ],
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        print(f"ERROR: PyInstaller failed with exit code {result.returncode}.")
        return result.returncode
    return verify()


def verify() -> int:
    if not EXE.exists():
        print(f"ERROR: expected executable was not created: {EXE}")
        return 1
    subsystem = pe_subsystem(EXE)
    if subsystem != IMAGE_SUBSYSTEM_WINDOWS_GUI:
        print(f"ERROR: {EXE} is not a windowed executable (PE subsystem {subsystem}).")
        return 1
    digest = hashlib.sha256(EXE.read_bytes()).hexdigest()
    print(f"Built  {EXE}")
    print(f"Size   {EXE.stat().st_size:,} bytes")
    print(f"SHA256 {digest}")
    print("Subsystem IMAGE_SUBSYSTEM_WINDOWS_GUI (no console window)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-process-check",
        action="store_true",
        help="build even if AgentOps.exe is already running (it may fail to replace it)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="after building, launch the executable and verify the GUI starts",
    )
    args = parser.parse_args(argv)

    status = build(args.skip_process_check)
    if status != 0:
        return status
    if args.smoke:
        sys.path.insert(0, str(ROOT / "scripts"))
        import smoke_windows_exe

        return smoke_windows_exe.main([])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())