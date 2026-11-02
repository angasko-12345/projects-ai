"""JSON helpers shared by the state mixins."""

from __future__ import annotations

import json


def _jsonable(value: object) -> object:
    """Deep-coerce a value into JSON-serializable data (never raises)."""
    try:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return (
                value
                if value == value and value not in (float("inf"), float("-inf"))
                else str(value)
            )
        if isinstance(value, dict):
            return {str(_jsonable_key(key)): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return _safe_json_repr(value)
    except Exception:
        return _safe_json_repr(value)


def _jsonable_key(key: object) -> str:
    if isinstance(key, str):
        return key
    try:
        return str(key)
    except Exception:
        return "<unrepresentable>"


def _safe_json_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        try:
            return f"<unrepresentable {type(value).__name__}>"
        except Exception:
            return "<unrepresentable>"


def _json(value: object | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: object) -> object:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
