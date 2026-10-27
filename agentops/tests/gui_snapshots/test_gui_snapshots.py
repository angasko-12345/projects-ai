"""Pixel and label checks over every view of the real seeded MainWindow.

The window comes from ``scripts/gui_snapshots.py`` (same seed, same real
``AgentOpsController``) at 1100x700 on the offscreen platform. Two checks:

(a) brightness: per view, the share of pixels with luminance > 0.85 is <= 2%.
    Expected to fail until the app installs a dark QPalette (v2 plan, phase A):
    unstyled Qt surfaces still render light-grey/white on the dark theme.
(b) clipped labels: no visible, non-word-wrapped QLabel with text is shorter
    than its font's line height. Expected to fail until the Workflows pipeline
    cards stop collapsing.

Set ``AGENTOPS_SNAPSHOT_OUT`` to also save the PNGs this test grabs
(``tests/gui_snapshots/out/`` is git-ignored for that purpose).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import qt_app, requires_qt
except ModuleNotFoundError:
    from qt_display import qt_app, requires_qt

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 1100, 700
BRIGHT_LUMINANCE = 0.85
MAX_BRIGHT_SHARE = 0.02


def _load_script():
    """Import scripts/gui_snapshots.py (scripts/ is not a package)."""
    name = "agentops_gui_snapshots"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "gui_snapshots.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve the module by name
    spec.loader.exec_module(module)
    return module


def bright_share(image) -> float:
    """Share of pixels whose luminance exceeds BRIGHT_LUMINANCE.

    Luminance is Qt's Grayscale8 conversion (about Rec.601 luma), scaled to 0..1.
    Counting byte values in C keeps this fast without numpy.
    """
    from PySide6.QtGui import QImage

    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height, stride = gray.width(), gray.height(), gray.bytesPerLine()
    if not width or not height:
        return 0.0
    raw = bytes(gray.constBits())
    rows = b"".join(raw[y * stride : y * stride + width] for y in range(height))
    cutoff = int(BRIGHT_LUMINANCE * 255)  # values strictly above 0.85 * 255
    bright = sum(rows.count(bytes([value])) for value in range(cutoff + 1, 256))
    return bright / (width * height)


def clipped_labels(root) -> list[str]:
    """Visible, non-word-wrapped QLabels with text shorter than one text line."""
    from PySide6.QtWidgets import QLabel

    offenders = []
    for label in root.findChildren(QLabel):
        text = label.text().strip()
        if not text or label.wordWrap() or not label.isVisible():
            continue
        line = label.fontMetrics().height()
        if label.height() < line:
            name = label.objectName() or type(label).__name__
            offenders.append(f"{name} {text[:40]!r} h={label.height()} < {line}")
    return offenders


@requires_qt
class GuiSnapshotTest(unittest.TestCase):
    shares: dict[str, float] = {}
    clipped: dict[str, list[str]] = {}

    @classmethod
    def setUpClass(cls):
        app = qt_app()
        # Discovery runs this package before the tests/test_*.py modules, so the
        # app-wide font and stylesheet set here must not leak into them.
        cls._saved_app = (app.font(), app.styleSheet())
        cls.addClassCleanup(cls._restore_app, app)
        snap = _load_script()
        snap.configure_app()
        cls._base = Path(tempfile.mkdtemp(prefix="agentops-snaptest-"))
        out = os.environ.get("AGENTOPS_SNAPSHOT_OUT")
        out_dir = Path(out) if out else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        try:
            seeded = snap.seed(cls._base)
            with snap.no_blocking_dialogs():
                window = snap.open_window(
                    [seeded.repo, seeded.second_repo], cls._base / "settings.json", WIDTH, HEIGHT
                )
                try:
                    for view_id in snap.iter_views(window, settle_ms=500):
                        image = window.grab().toImage()
                        if out_dir:
                            image.save(str(out_dir / f"{view_id}-{WIDTH}x{HEIGHT}.png"))
                        cls.shares[view_id] = bright_share(image)
                        cls.clipped[view_id] = clipped_labels(window)
                finally:
                    snap.close_window(window)
        except BaseException:
            shutil.rmtree(cls._base, ignore_errors=True)
            raise

    @classmethod
    def _restore_app(cls, app):
        font, stylesheet = cls._saved_app
        app.setStyleSheet(stylesheet)
        app.setFont(font)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._base, ignore_errors=True)

    def test_every_view_was_captured(self):
        from agentops.gui.views import VIEW_SPECS

        self.assertEqual(list(self.shares), [view_id for view_id, _t, _f in VIEW_SPECS])

    @unittest.expectedFailure  # no QPalette yet: light Qt defaults leak into the dark theme
    def test_views_have_almost_no_bright_pixels(self):
        offenders = {
            view_id: round(share, 4)
            for view_id, share in self.shares.items()
            if share > MAX_BRIGHT_SHARE
        }
        self.assertEqual({}, offenders, f"bright-pixel share > {MAX_BRIGHT_SHARE:.0%}")

    # Fails on main: in the Workflows view the pipeline step cards of the selected
    # workflow are squeezed to 0-12 px, so their title, badge, agent, model,
    # duration and "after <step>" labels are clipped (v1 rendering bug; the v2
    # plan's workspace phase replaces that pane). Remove the marker once fixed.
    @unittest.expectedFailure
    def test_no_label_is_shorter_than_its_text_line(self):
        offenders = {view_id: found for view_id, found in self.clipped.items() if found}
        self.assertEqual({}, offenders)


if __name__ == "__main__":
    unittest.main()
