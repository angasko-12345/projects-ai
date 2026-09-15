"""Build the reproducible Windows AgentOps desktop executable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "AgentOps.spec"
EXE = ROOT / "dist" / "AgentOps.exe"


def main() -> int:
    if not SPEC.exists():
        print(f"ERROR: missing PyInstaller specification: {SPEC}")
        return 1
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=ROOT,
        check=True,
    )
    if not EXE.exists():
        print(f"ERROR: expected executable was not created: {EXE}")
        return 1
    print(f"Built {EXE} ({EXE.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
