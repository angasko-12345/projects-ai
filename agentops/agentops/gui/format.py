"""Presentation formatters (Qt-free).

Shared by every view so durations, timestamps, and identifiers render
identically. Pure functions only - importable and testable without PySide6.
"""

from __future__ import annotations


def format_duration(seconds: object) -> str:
    """Human duration for tables: ``0.4s``, ``12.3s``, ``2m 05s``, ``1h 04m``."""
    if seconds is None:
        return "-"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if value < 0:
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    total_minutes = int(value // 60)
    remaining = int(round(value - total_minutes * 60))
    if remaining == 60:
        total_minutes += 1
        remaining = 0
    if total_minutes < 60:
        return f"{total_minutes}m {remaining:02d}s"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes:02d}m"


def format_timestamp(value: object, length: int = 19) -> str:
    """ISO timestamp trimmed to seconds (or ``-`` for empty values)."""
    text = "" if value is None else str(value)
    if not text:
        return "-"
    text = text.replace("T", " ")
    return text[:length]


def elide(value: object, limit: int = 120) -> str:
    """Single-line an arbitrary string, truncating with an ellipsis."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def format_size(size: object) -> str:
    """Byte counts for artifact/log tables."""
    try:
        count = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    if count < 1024 * 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MB"
    return f"{count / (1024 * 1024 * 1024):.1f} GB"


def short_id(value: object, length: int = 8) -> str:
    """Shorten workflow/task ids for dense tables while staying recognizable."""
    text = str(value or "")
    if len(text) <= length:
        return text
    return text[:length]
