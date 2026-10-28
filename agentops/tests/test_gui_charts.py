"""QPainter dashboard charts (gui/charts.py): paint without raising, draw pixels."""

from __future__ import annotations

import unittest

try:  # `discover -s tests` puts tests/ on sys.path; direct runs do not.
    from tests.qt_display import qt_app, requires_qt
except ModuleNotFoundError:
    from qt_display import qt_app, requires_qt

BACKGROUND = "#0f1628"  # navy_surface: what a chart card paints behind the chart


def render(widget, width: int = 320, height: int = 180):  # -> (QImage, painted pixel count)
    from PySide6.QtGui import QColor, QPixmap

    widget.resize(width, height)
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(BACKGROUND))
    widget.render(pixmap)
    image = pixmap.toImage()
    background = QColor(BACKGROUND).rgb()
    painted = sum(
        1
        for y in range(0, height, 2)
        for x in range(0, width, 2)
        if image.pixel(x, y) != background
    )
    return image, painted


class NiceStepTest(unittest.TestCase):
    @requires_qt
    def test_integer_steps_cover_the_peak(self):
        from agentops.gui.charts import nice_step

        self.assertEqual(nice_step(0), 1)
        self.assertEqual(nice_step(3), 1)
        self.assertEqual(nice_step(6), 2)
        self.assertEqual(nice_step(8), 2)
        self.assertEqual(nice_step(17), 5)
        self.assertEqual(nice_step(230), 100)
        for peak in (1, 7, 9, 41, 99, 501):
            self.assertGreaterEqual(nice_step(peak) * 4, peak)


@requires_qt
class ChartPaintTest(unittest.TestCase):
    def setUp(self):
        qt_app()

    def _area(self, values_a, values_b, highlight=None, tooltip=""):
        from agentops.gui.charts import AreaChart

        labels = [f"Oct {14 + i}" for i in range(len(values_a))]
        return AreaChart(
            labels,
            [("runs", "#5eead4", values_a), ("failed", "#f2615a", values_b)],
            highlight,
            tooltip,
        )

    def test_area_chart_empty_one_point_and_all_zero(self):
        for chart in (
            self._area([], []),
            self._area([3], [1], 0, "Oct 14 · 3 runs"),
            self._area([0] * 14, [0] * 14, 13, "Oct 27 · 0 runs"),
        ):
            _image, painted = render(chart)
            self.assertGreater(painted, 0)

    def test_area_curve_never_dips_below_the_baseline(self):
        from PySide6.QtCore import QPointF

        from agentops.gui.charts import smooth_path

        points = [QPointF(0, 50), QPointF(10, 100), QPointF(20, 100), QPointF(30, 0)]
        rect = smooth_path(points).boundingRect()
        self.assertLessEqual(rect.bottom(), 100.0)  # y=100 is the zero baseline
        self.assertGreaterEqual(rect.top(), 0.0)

    def test_area_tooltip_flips_at_the_right_edge(self):
        values = [1, 2, 3, 4, 5, 6, 8]
        chart = self._area(values, [0] * 7, 6, "Oct 20 · 8 runs")
        image, painted = render(chart, 300, 180)
        self.assertGreater(painted, 0)
        # The bubble fills in the text colour. At the last point (x=288) a
        # centred bubble would run off the right edge, so it must flip to
        # the left of the point: bubble pixels exist, none right of x=284.
        from PySide6.QtGui import QColor

        from agentops.gui.tokens import DARK

        bubble = QColor(DARK.text).rgb()
        xs = [x for x in range(300) for y in range(0, 180, 2) if image.pixel(x, y) == bubble]
        self.assertTrue(xs, "no tooltip bubble painted")
        self.assertLess(max(xs), 284)

    def test_donut_empty_single_and_split(self):
        from agentops.gui.charts import Donut

        for donut in (
            Donut([], "", ""),
            Donut([(5, "#3fb950")], "100%", "pass rate"),
            Donut([(0, "#3fb950"), (0, "#f2615a")], "-", "no runs"),
            Donut([(12, "#3fb950"), (2, "#f2615a")], "86%", "pass rate"),
        ):
            _image, painted = render(donut, 160, 160)
            self.assertGreater(painted, 0)

    def test_bar_row_empty_full_and_over(self):
        from agentops.gui.charts import BarRow

        for value, maximum, fraction in ((0, 0, 0.0), (0, 5, 0.0), (5, 5, 1.0), (9, 3, 1.0)):
            bar = BarRow(value, maximum)
            self.assertEqual(bar.fraction(), fraction)
            _image, painted = render(bar, 120, bar.height())
            self.assertGreater(painted, 0)  # the navy track always shows


if __name__ == "__main__":
    unittest.main()
