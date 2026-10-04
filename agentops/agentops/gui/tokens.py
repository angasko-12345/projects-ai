"""Design tokens for the AgentOps desktop client.

Qt-free on purpose: tokens are plain data plus a QSS string builder, so they
can be imported and tested without PySide6. The dark theme is the shipped
default; every widget derives its colors from :data:`DARK` through
:func:`build_stylesheet` and :func:`status_colors`, never from literals. A
future light theme is a second :class:`Theme` instance plus a rebuild of the
stylesheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Bundled image assets (combo arrows, etc.); QSS needs filesystem paths.
ASSET_DIR = Path(__file__).resolve().parent / "assets"


@dataclass(frozen=True)
class Theme:
    name: str

    # Surfaces
    bg: str
    surface: str
    surface_alt: str
    surface_hover: str
    border: str
    border_strong: str

    # Text
    text: str
    text_muted: str
    text_faint: str

    # Accents and status
    accent: str
    accent_hover: str
    success: str
    warning: str
    danger: str
    info: str

    # Typography
    font_family: str
    font_mono: str
    size_small: int
    size_base: int
    size_title: int
    size_heading: int

    # Spacing and shape
    space_xs: int
    space_sm: int
    space_md: int
    space_lg: int
    space_xl: int
    radius_sm: int
    radius_md: int
    radius_lg: int


DARK = Theme(
    name="dark",
    bg="#0e1116",
    surface="#151a21",
    surface_alt="#1b212b",
    surface_hover="#232b36",
    border="#262e3a",
    border_strong="#36404f",
    text="#e6eaf0",
    text_muted="#9aa5b4",
    text_faint="#6b7686",
    accent="#4c8dff",
    accent_hover="#6ba2ff",
    success="#3fb950",
    warning="#d29922",
    danger="#f0554e",
    info="#58a6ff",
    font_family="Segoe UI",
    font_mono="Cascadia Mono, Consolas",
    size_small=11,
    size_base=13,
    size_title=15,
    size_heading=19,
    space_xs=4,
    space_sm=8,
    space_md=12,
    space_lg=16,
    space_xl=24,
    radius_sm=3,
    radius_md=5,
    radius_lg=8,
)


def with_alpha(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` + alpha in 0..1 -> ``rgba(r, g, b, a)`` for QSS."""
    value = hex_color.lstrip("#")
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha))})"


# Lifecycle status -> (foreground, background) badge colors. Unknown statuses
# fall back to the neutral pair so a new status never renders invisible.
_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "passed": ("success", "success"),
    "completed": ("success", "success"),
    "ready": ("success", "success"),
    "merged": ("success", "success"),
    "clean": ("success", "success"),
    "running": ("accent", "accent"),
    "starting": ("accent", "accent"),
    "active": ("accent", "accent"),
    "in_progress": ("accent", "accent"),
    "pending": ("text_muted", "text_muted"),
    "queued": ("text_muted", "text_muted"),
    "blocked": ("warning", "warning"),
    "paused": ("warning", "warning"),
    "dirty": ("warning", "warning"),
    "failed": ("danger", "danger"),
    "timed_out": ("danger", "danger"),
    "terminated": ("danger", "danger"),
    "error": ("danger", "danger"),
    "conflict": ("danger", "danger"),
    "cancelled": ("text_faint", "text_faint"),
    "canceled": ("text_faint", "text_faint"),
    "interrupted": ("warning", "warning"),
    "skipped": ("text_faint", "text_faint"),
    "unknown": ("text_faint", "text_faint"),
}

_STATUS_ALIASES = {
    "timed-out": "timed_out",
    "timedout": "timed_out",
    "not-run": "skipped",
    "not_run": "skipped",
}


def status_colors(status: object, theme: Theme = DARK) -> tuple[str, str]:
    """Badge colors for any lifecycle status value.

    Accepts enums (``.value``), ``None``, and arbitrary strings; returns
    ``(foreground, background)`` hex colors.
    """
    value = getattr(status, "value", status)
    key = str(value or "unknown").strip().lower()
    key = _STATUS_ALIASES.get(key, key)
    foreground_token, background_token = _STATUS_COLORS.get(key, ("text_faint", "text_faint"))
    return getattr(theme, foreground_token), getattr(theme, background_token)


def status_tint(status: object, theme: Theme = DARK, alpha: float = 0.16) -> str:
    """Translucent background tint for status pills and row highlights."""
    return with_alpha(status_colors(status, theme)[1], alpha)


def build_stylesheet(theme: Theme = DARK) -> str:
    """Full Qt stylesheet for the theme."""
    t = theme
    return f"""
/* ---- base ---- */
QMainWindow, QDialog {{
    background: {t.bg};
}}
QWidget {{
    color: {t.text};
    font-family: "{t.font_family}";
    font-size: {t.size_base}px;
}}
QLabel {{
    background: transparent;
}}
QLabel[role="heading"] {{
    font-size: {t.size_heading}px;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="title"] {{
    font-size: {t.size_title}px;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="subtitle"] {{
    color: {t.text_muted};
    font-size: {t.size_small}px;
}}
QLabel[role="muted"] {{
    color: {t.text_muted};
}}
QLabel[role="faint"] {{
    color: {t.text_faint};
    font-size: {t.size_small}px;
}}
QLabel[role="mono"] {{
    font-family: "{t.font_mono}";
}}
QLabel[role="stat"] {{
    font-size: {t.size_heading}px;
    font-weight: 600;
}}
QLabel[role="page"] {{
    font-size: {t.size_heading - 2}px;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="section"] {{
    font-size: {t.size_base}px;
    font-weight: 600;
    color: {t.text};
}}
QLabel[role="group"] {{
    font-size: {t.size_small}px;
    color: {t.text_faint};
    padding-left: {t.space_sm + 3}px;
}}

/* ---- sidebar ---- */
QWidget#Sidebar {{
    background: {t.surface};
    border-right: 1px solid {t.border};
}}
QPushButton[nav="true"] {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: {t.radius_md}px;
    color: {t.text_muted};
    text-align: left;
    padding: {t.space_sm - 1}px {t.space_md}px;
    font-size: {t.size_base}px;
}}
QPushButton[nav="true"]:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
QPushButton[nav="true"]:pressed {{
    background: {t.surface_alt};
}}
QPushButton[nav="true"]:focus {{
    background: {t.surface_hover};
    color: {t.text};
}}
QPushButton[nav="true"]:checked {{
    background: {with_alpha(t.accent, 0.13)};
    border-left: 2px solid {t.accent};
    color: {t.text};
}}

/* ---- top bar ---- */
QWidget#TopBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}

/* ---- buttons ---- */
QPushButton {{
    background: {t.surface_alt};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_md}px;
    color: {t.text};
    padding: {t.space_sm - 1}px {t.space_md}px;
    min-height: {t.size_base + 4}px;
}}
QPushButton:hover {{
    background: {t.surface_hover};
    border-color: {t.border_strong};
}}
QPushButton:pressed {{
    background: {t.surface};
}}
QPushButton:disabled {{
    color: {t.text_faint};
    background: {t.surface};
    border-color: {t.border};
}}
QPushButton[variant="primary"] {{
    background: {t.accent};
    border: 1px solid {t.accent};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton[variant="primary"]:disabled {{
    background: {with_alpha(t.accent, 0.35)};
    border-color: transparent;
    color: {with_alpha("#ffffff", 0.7)};
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.text_muted};
    padding: {t.space_sm - 1}px {t.space_md}px;
}}
QPushButton[variant="ghost"]:hover {{
    background: {t.surface_hover};
    color: {t.text};
    border-radius: {t.radius_md}px;
}}
QPushButton[variant="danger"] {{
    background: transparent;
    border: 1px solid {with_alpha(t.danger, 0.5)};
    color: {t.danger};
}}
QPushButton[variant="danger"]:hover {{
    background: {with_alpha(t.danger, 0.14)};
}}
QPushButton[variant="danger"]:disabled {{
    border-color: {t.border};
    color: {t.text_faint};
    background: transparent;
}}
QPushButton:focus {{
    border-color: {t.accent};
}}

/* ---- inputs ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox {{
    background: {t.bg};
    border: 1px solid {t.border};
    border-radius: {t.radius_md}px;
    color: {t.text};
    padding: {t.space_sm}px {t.space_sm + 2}px;
    selection-background-color: {with_alpha(t.accent, 0.45)};
    selection-color: {t.text};
}}
QLineEdit, QComboBox, QSpinBox {{
    min-height: {t.size_base + 4}px;
    padding: {t.space_sm - 1}px {t.space_sm + 2}px;
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {t.accent};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {t.text_faint};
    background: {t.surface};
}}
QLineEdit[role="search"] {{
    padding-left: {t.space_lg}px;
}}
QComboBox {{
    padding-right: {t.space_lg + t.space_sm}px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: {t.space_lg}px;
}}
QComboBox::down-arrow {{
    image: url("{(ASSET_DIR / 'chevron-down.svg').as_posix()}");
    margin-right: {t.space_sm}px;
}}
QComboBox QAbstractItemView {{
    background: {t.surface_alt};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_md}px;
    color: {t.text};
    selection-background-color: {with_alpha(t.accent, 0.25)};
    selection-color: {t.text};
    outline: none;
    padding: {t.space_xs}px;
}}
QCheckBox, QRadioButton {{
    color: {t.text};
    spacing: {t.space_sm}px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {t.border_strong};
    background: {t.bg};
}}
QCheckBox::indicator {{
    border-radius: {t.radius_sm}px;
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent};
}}
QRadioButton::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent};
}}

/* ---- tables ---- */
QTableView, QTreeView {{
    background: {t.surface};
    alternate-background-color: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: {t.radius_md}px;
    gridline-color: {t.border};
    selection-background-color: {with_alpha(t.accent, 0.22)};
    selection-color: {t.text};
    outline: none;
}}
QTableView::item, QTreeView::item {{
    padding: {t.space_sm - 2}px {t.space_sm}px;
    border: none;
}}
QTableView::item:hover, QTreeView::item:hover {{
    background: {t.surface_hover};
}}
QHeaderView::section {{
    background: {t.surface_alt};
    color: {t.text_muted};
    border: none;
    border-bottom: 1px solid {t.border};
    border-right: 1px solid {t.border};
    padding: {t.space_sm}px {t.space_sm + 2}px;
    font-size: {t.size_small}px;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    color: {t.text};
    background: {t.surface_hover};
}}
QHeaderView::down-arrow, QHeaderView::up-arrow {{
    image: none;
}}

/* ---- lists ---- */
QListWidget, QListView {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_md}px;
    color: {t.text};
    outline: none;
    padding: {t.space_xs}px;
}}
QListWidget::item {{
    padding: {t.space_sm}px;
    border-radius: {t.radius_sm}px;
}}
QListWidget::item:selected {{
    background: {with_alpha(t.accent, 0.20)};
    color: {t.text};
}}
QListWidget::item:hover {{
    background: {t.surface_hover};
}}

/* ---- panels ---- */
QFrame[card="true"] {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_lg}px;
}}
QFrame[role="divider"] {{
    background: {t.border};
    border: none;
    max-height: 1px;
}}
QGroupBox {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_lg}px;
    margin-top: {t.space_lg}px;
    padding: {t.space_md}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {t.space_md}px;
    padding: 0 {t.space_xs}px;
    color: {t.text_muted};
}}

/* ---- split handles ---- */
QSplitter::handle {{
    background: {t.border};
    width: 1px;
    height: 1px;
}}
QSplitter::handle:hover {{
    background: {t.border_strong};
}}

/* ---- progress ---- */
QProgressBar {{
    background: {t.bg};
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    color: {t.text};
    text-align: center;
    height: {t.space_sm + 4}px;
    max-height: {t.space_lg}px;
}}
QProgressBar::chunk {{
    background: {t.accent};
    border-radius: {t.radius_sm}px;
}}

/* ---- scrollbars ---- */
QScrollBar:vertical {{
    background: transparent;
    width: {t.space_lg}px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t.border_strong};
    border-radius: {t.space_sm}px;
    min-height: {t.space_xl}px;
    margin: {t.space_xs}px {t.space_xs}px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.text_faint};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {t.space_lg}px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {t.border_strong};
    border-radius: {t.space_sm}px;
    min-width: {t.space_xl}px;
    margin: {t.space_xs}px {t.space_xs}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t.text_faint};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QScrollBar:corner {{
    background: transparent;
}}

/* ---- overlays ---- */
QWidget#ToastHost {{
    background: transparent;
}}
QFrame#Toast {{
    background: {t.surface_alt};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_md}px;
}}
QWidget#PaletteOverlay {{
    background: {with_alpha("#000000", 0.45)};
}}
QFrame#Palette {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_lg}px;
}}
QToolTip {{
    background: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border_strong};
    padding: {t.space_xs}px {t.space_sm}px;
    font-size: {t.size_small}px;
}}
QStatusBar {{
    background: {t.surface};
    color: {t.text_muted};
    border-top: 1px solid {t.border};
    font-size: {t.size_small}px;
}}
QMenu {{
    background: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_md}px;
    padding: {t.space_xs}px;
}}
QMenu::item {{
    padding: {t.space_sm}px {t.space_xl}px {t.space_sm}px {t.space_md}px;
    border-radius: {t.radius_sm}px;
}}
QMenu::item:selected {{
    background: {with_alpha(t.accent, 0.20)};
}}
QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: {t.space_xs}px {t.space_sm}px;
}}
"""
