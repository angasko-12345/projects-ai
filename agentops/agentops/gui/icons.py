"""Lucide icons (ISC, ``assets/LICENSE-lucide.txt``) rendered as tinted pixmaps.

The SVGs ship flat as ``assets/lucide-<name>.svg`` so ``resource_path`` and
the frozen bundle find them like any other asset. ``icon()`` swaps the
stroke's ``currentColor`` for the requested colour and renders with
``QSvgRenderer`` at the device pixel ratio, so an icon stays crisp at 125%,
150% and 200% scaling. Results are cached per (name, colour, size, ratio).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .tokens import ASSET_DIR

# Ratios pre-rendered into a QIcon so Qt can pick the sharp one on any screen.
_ICON_RATIOS = (1.0, 1.25, 1.5, 2.0)

_svg_cache: dict[str, str] = {}
_pixmap_cache: dict[tuple[str, str, int, float], QPixmap] = {}


def icon_path(name: str) -> Path:
    """Bundled SVG for a Lucide icon name (``"circle-check"``)."""
    return ASSET_DIR / f"lucide-{name}.svg"


def _svg_source(name: str) -> str:
    source = _svg_cache.get(name)
    if source is None:
        path = icon_path(name)
        if not path.is_file():
            raise KeyError(f"unknown icon {name!r}: {path} is missing")
        source = path.read_text(encoding="utf-8")
        _svg_cache[name] = source
    return source


def _screen_ratio() -> float:
    app = QGuiApplication.instance()
    return float(app.devicePixelRatio()) if app is not None else 1.0


def icon(name: str, color: str, size: int = 16, ratio: float | None = None) -> QPixmap:
    """``size`` x ``size`` logical-pixel pixmap of ``name`` stroked in ``color``.

    ``ratio`` defaults to the application's device pixel ratio; widgets on a
    particular screen pass their own ``devicePixelRatioF()``.
    """
    scale = float(ratio) if ratio else _screen_ratio()
    key = (name, color, int(size), round(scale, 3))
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached
    svg = _svg_source(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    side = max(1, round(size * scale))
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, side, side))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    _pixmap_cache[key] = pixmap
    return pixmap


def qicon(name: str, color: str, size: int = 16, checked_color: str | None = None) -> QIcon:
    """QIcon with crisp pixmaps for the common scale factors.

    ``checked_color`` (optional) tints the On state, for checkable buttons.
    """
    result = QIcon()
    for scale in _ICON_RATIOS:
        result.addPixmap(icon(name, color, size, scale), QIcon.Mode.Normal, QIcon.State.Off)
        if checked_color:
            result.addPixmap(
                icon(name, checked_color, size, scale), QIcon.Mode.Normal, QIcon.State.On
            )
    return result


__all__ = ["icon", "icon_path", "qicon"]
