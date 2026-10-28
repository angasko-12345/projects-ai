"""Dashboard charts painted with QPainter (no QtCharts dependency).

QtCharts ships in PySide6-Addons, not PySide6-Essentials, so the charts are
plain widgets that paint from ``tokens``: :class:`AreaChart` (runs over
time), :class:`Donut` (verification share) and :class:`BarRow` (one thin
bar). Each paints something sensible for empty, one-point and all-zero
input; deciding to show an empty state instead is the caller's job.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .tokens import DARK, Theme, qcolor, qfont

Series = tuple[str, str, Sequence[float]]  # (name, colour, values)


def nice_step(maximum: float, lines: int = 4) -> int:
    """Integer grid step so ``lines`` steps cover ``maximum`` (1, 2, 5 x 10^n)."""
    raw = max(1.0, float(maximum)) / lines
    magnitude = 1
    while magnitude * 10 <= raw:
        magnitude *= 10
    for factor in (1, 2, 5, 10):
        if factor * magnitude >= raw:
            return factor * magnitude
    return 10 * magnitude


def smooth_path(points: Sequence[QPointF]) -> QPainterPath:
    """Catmull-Rom curve through ``points`` as cubic Beziers.

    Each control point's y is clamped between its segment's two end points,
    so the curve never overshoots them (a run of zeros stays on the baseline
    instead of dipping below 0).
    """
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    last = len(points) - 1
    for i in range(last):
        p0, p1, p2 = points[max(0, i - 1)], points[i], points[i + 1]
        p3 = points[min(last, i + 2)]
        low, high = min(p1.y(), p2.y()), max(p1.y(), p2.y())
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6)
        c1.setY(min(high, max(low, c1.y())))
        c2.setY(min(high, max(low, c2.y())))
        path.cubicTo(c1, c2, p2)
    return path


class AreaChart(QWidget):
    """Smooth area chart: gradient under series 0, dashed series 1, tooltip."""

    _PAD = (30, 12, 12, 22)  # left (y labels), top, right, bottom (x labels)

    def __init__(
        self,
        labels: Sequence[str] = (),
        series: Sequence[Series] = (),
        highlight: int | None = None,
        tooltip: str = "",
        parent: QWidget | None = None,
        theme: Theme = DARK,
    ):
        super().__init__(parent)
        self._theme = theme
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_data(labels, series, highlight, tooltip)

    def set_data(
        self,
        labels: Sequence[str],
        series: Sequence[Series],
        highlight: int | None = None,
        tooltip: str = "",
    ) -> None:
        self._labels = [str(text) for text in labels]
        self._series = [
            (str(n), str(c), [max(0.0, float(v)) for v in vals]) for n, c, vals in series
        ]
        self._highlight = highlight
        self._tooltip = tooltip
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(560, 220)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(240, 150)

    def _font(self) -> QFont:
        return qfont(self._theme, self._theme.size_micro)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        t = self._theme
        left, top, right, bottom = self._PAD
        plot = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        if plot.width() < 10 or plot.height() < 10:
            return
        count = max([len(self._labels)] + [len(values) for _n, _c, values in self._series])
        peak = max([0.0] + [max(values, default=0.0) for _n, _c, values in self._series])
        step = nice_step(peak)
        ceiling = step * 4
        font = self._font()
        metrics = QFontMetrics(font)
        painter.setFont(font)

        # Grid: four dashed navy lines over a solid baseline, integer labels.
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            pen = QPen(QColor(t.navy_border), 1)
            if i:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(t.text_faint))
            painter.drawText(
                QRectF(0, y - 8, left - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(step * i),
            )
        if count == 0:
            return

        def x_at(index: int) -> float:
            if count == 1:
                return plot.center().x()
            return plot.left() + plot.width() * index / (count - 1)

        def point(index: int, value: float) -> QPointF:
            return QPointF(x_at(index), plot.bottom() - plot.height() * value / ceiling)

        self._paint_x_labels(painter, metrics, plot, x_at, count)
        for order, (_name, color, values) in enumerate(self._series[:2]):
            points = [point(i, v) for i, v in enumerate(values)]
            if not points:
                continue
            path = smooth_path(points) if len(points) > 1 else QPainterPath(points[0])
            if order == 0 and len(points) > 1:
                fill = QPainterPath(path)
                fill.lineTo(points[-1].x(), plot.bottom())
                fill.lineTo(points[0].x(), plot.bottom())
                fill.closeSubpath()
                gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
                gradient.setColorAt(0.0, qcolor(color, 0.32))
                gradient.setColorAt(1.0, qcolor(color, 0.0))
                painter.fillPath(fill, gradient)
            pen = QPen(QColor(color), 2 if order == 0 else 1.6)
            if order == 1:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if len(points) == 1:
                painter.setBrush(QColor(color))
                painter.drawEllipse(points[0], 3, 3)
            else:
                painter.drawPath(path)
        self._paint_highlight(painter, plot, point)

    def _paint_x_labels(self, painter, metrics, plot, x_at, count) -> None:  # type: ignore[no-untyped-def]
        """Every other label, never colliding with the forced last one."""
        if not self._labels:
            return
        painter.setPen(QColor(self._theme.text_faint))

        def box(index: int) -> QRectF:
            text = self._labels[index]
            width = metrics.horizontalAdvance(text) + 4
            x = min(max(x_at(index) - width / 2, 0.0), self.width() - width)
            return QRectF(x, plot.bottom() + 4, width, metrics.height())

        last = min(count, len(self._labels)) - 1
        last_box = box(last)
        drawn: QRectF | None = None
        for index in range(0, last, 2):
            candidate = box(index)
            if candidate.adjusted(-6, 0, 6, 0).intersects(last_box):
                continue
            if drawn is not None and candidate.adjusted(-6, 0, 6, 0).intersects(drawn):
                continue
            painter.drawText(candidate, Qt.AlignmentFlag.AlignCenter, self._labels[index])
            drawn = candidate
        painter.drawText(last_box, Qt.AlignmentFlag.AlignCenter, self._labels[last])

    def _paint_highlight(self, painter, plot, point) -> None:  # type: ignore[no-untyped-def]
        t = self._theme
        if self._highlight is None or not self._series:
            return
        _name, color, values = self._series[0]
        if not 0 <= self._highlight < len(values):
            return
        anchor = point(self._highlight, values[self._highlight])
        guide = QPen(QColor(t.text_faint), 1, Qt.PenStyle.DashLine)
        painter.setPen(guide)
        painter.drawLine(anchor, QPointF(anchor.x(), plot.bottom()))
        painter.setPen(QPen(QColor(color), 2))
        painter.setBrush(QColor(t.navy_surface))
        painter.drawEllipse(anchor, 4.5, 4.5)
        if not self._tooltip:
            return
        font = qfont(t, t.size_caption)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        bubble = QRectF(0, 0, metrics.horizontalAdvance(self._tooltip) + 20, metrics.height() + 10)
        bubble.moveBottom(anchor.y() - 10)
        bubble.moveLeft(anchor.x() - bubble.width() / 2)
        if bubble.top() < 0:  # flip below the point
            bubble.moveTop(anchor.y() + 10)
        if bubble.right() > self.width() - 2:  # flip to the left of the point
            bubble.moveRight(min(anchor.x() - 8, self.width() - 2))
        if bubble.left() < 2:
            bubble.moveLeft(max(anchor.x() + 8, 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.text))
        painter.drawRoundedRect(bubble, t.radius_sm, t.radius_sm)
        painter.setPen(QColor(t.bg))
        painter.setFont(font)
        painter.drawText(bubble, Qt.AlignmentFlag.AlignCenter, self._tooltip)


class Donut(QWidget):
    """Gapped ring (20px, 3 degree gaps) over a navy track, with centre text."""

    RING = 20
    GAP = 3.0

    def __init__(
        self,
        segments: Sequence[tuple[float, str]] = (),
        center_big: str = "",
        center_small: str = "",
        parent: QWidget | None = None,
        theme: Theme = DARK,
    ):
        super().__init__(parent)
        self._theme = theme
        self.set_data(segments, center_big, center_small)

    def set_data(
        self, segments: Sequence[tuple[float, str]], big: str = "", small: str = ""
    ) -> None:
        self._segments = [(max(0.0, float(v)), str(c)) for v, c in segments]
        self._big = big
        self._small = small
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(150, 150)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(110, 110)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        t = self._theme
        side = min(self.width(), self.height()) - self.RING
        if side <= 0:
            return
        ring = QRectF(0, 0, side, side)
        ring.moveCenter(QRectF(self.rect()).center())
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(t.navy_border), self.RING)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(ring, 0, 360 * 16)
            shown = [(v, c) for v, c in self._segments if v > 0]
            total = sum(v for v, _c in shown)
            start = 90.0  # 12 o'clock, then clockwise
            gap = self.GAP if len(shown) > 1 else 0.0
            for value, color in shown:
                span = 360.0 * value / total
                pen.setColor(QColor(color))
                painter.setPen(pen)
                sweep = max(0.5, span - gap)
                painter.drawArc(ring, round((start - gap / 2) * 16), -round(sweep * 16))
                start -= span
            self._paint_center(painter, ring)
        finally:
            painter.end()

    def _paint_center(self, painter: QPainter, ring: QRectF) -> None:
        t = self._theme
        big = qfont(t, t.size_stat)
        big.setWeight(QFont.Weight.DemiBold)
        small = qfont(t, t.size_caption)
        big_h = QFontMetrics(big).height() if self._big else 0
        small_h = QFontMetrics(small).height() if self._small else 0
        y = ring.center().y() - (big_h + small_h) / 2
        if self._big:
            painter.setPen(QColor(t.text))
            painter.setFont(big)
            painter.drawText(
                QRectF(ring.left(), y, ring.width(), big_h), Qt.AlignmentFlag.AlignCenter, self._big
            )
        if self._small:
            painter.setPen(QColor(t.text_muted))
            painter.setFont(small)
            box = QRectF(ring.left(), y + big_h, ring.width(), small_h)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self._small)


class BarRow(QWidget):
    """One thin rounded bar: ``value`` of ``maximum`` filled over a navy track."""

    HEIGHT = 6

    def __init__(
        self,
        value: float = 0,
        maximum: float = 1,
        color: str | None = None,
        parent: QWidget | None = None,
        theme: Theme = DARK,
    ):
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(self.HEIGHT + 4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_value(value, maximum, color)

    def set_value(self, value: float, maximum: float, color: str | None = None) -> None:
        self._value = max(0.0, float(value))
        self._maximum = max(0.0, float(maximum))
        self._color = color or self._theme.series_2
        self.update()

    def fraction(self) -> float:
        return min(1.0, self._value / self._maximum) if self._maximum else 0.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(80, self.HEIGHT + 4)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        track = QRectF(0, (self.height() - self.HEIGHT) / 2, self.width(), self.HEIGHT)
        radius = self.HEIGHT / 2
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._theme.navy_border))
            painter.drawRoundedRect(track, radius, radius)
            if self.fraction() > 0:
                filled = QRectF(track)
                filled.setWidth(max(self.HEIGHT, track.width() * self.fraction()))
                painter.setBrush(QColor(self._color))
                painter.drawRoundedRect(filled, radius, radius)
        finally:
            painter.end()


__all__ = ["AreaChart", "BarRow", "Donut", "nice_step", "smooth_path"]
