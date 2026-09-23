"""Basic configuration system: load a YAML config file into a plain dict."""
from __future__ import annotations

from pathlib import Path


def load_config(path: str | Path) -> dict:
    """Load YAML config from *path*; raises informative error if pyyaml is missing."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml is required to load configs "
            "(pip install -r requirements.txt)"
        ) from exc
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Config {path} must contain a top-level mapping")
    return data
