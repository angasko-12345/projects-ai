# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller specification for the AgentOps Windows desktop application.

Every path is derived from ``SPECPATH`` (the directory holding this file), so
the build does not depend on the current working directory and
``python scripts/build_windows_exe.py`` produces the same bundle from anywhere.
"""

import glob
import os

ROOT = SPECPATH
ENTRY = os.path.join(ROOT, "agentops_gui.py")

# Data files resolve at runtime through agentops.paths.resource_path(), which
# reads sys._MEIPASS when frozen. The destination column below must match the
# component list passed there.
DATA_FILES = [
    (os.path.join(ROOT, "agentops", "agents", "agents.yaml"), "agents"),
    # Every GUI asset ships: the combo chevron, the Lucide icons
    # (lucide-*.svg) and their licence (LICENSE-lucide.txt). PyInstaller
    # expands these globs, so a new icon needs no edit here.
    (os.path.join(ROOT, "agentops", "gui", "assets", "*.svg"), "agentops/gui/assets"),
    (os.path.join(ROOT, "agentops", "gui", "assets", "*.txt"), "agentops/gui/assets"),
]

missing = [source for source, _ in DATA_FILES if not glob.glob(source)]
if missing:
    raise SystemExit("Missing bundled data: " + ", ".join(missing))

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing in the package imports tkinter; excluding it keeps the Tcl/Tk
    # runtime out of the bundle.
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AgentOps',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts Qt DLLs; compression stays off.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir="",
    # Windowed: double-clicking opens the GUI without a console window.
    console=False,
    # Last-resort traceback dialog if startup fails outside our own handler.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
