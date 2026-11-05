"""YAML config loading. Never print secrets."""
import os

import yaml


class ConfigError(Exception):
    pass


def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_config_path():
    return os.path.join(get_project_root(), "config", "channel.yaml")


def load_config(path=None):
    cfg_path = path or default_config_path()
    if not os.path.isfile(cfg_path):
        raise ConfigError(f"config file not found: {cfg_path}")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {cfg_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config must be a mapping: {cfg_path}")
    return data


def get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def get_runs_dir(cfg=None, path=None):
    """Runs dir: explicit path else <project>/runs."""
    return path or os.path.join(get_project_root(), "runs")


def ensure_d_confined(path):
    """Raise ConfigError unless `path` is on D: or inside the project dir.

    Windows-only: on other platforms there is no drive letter to enforce. This
    guards installs, models and caches, not pytest temp dirs.
    """
    resolved = os.path.abspath(path)
    if os.name != "nt":
        return resolved
    project = os.path.abspath(get_project_root())
    drive = os.path.splitdrive(resolved)[0].upper()
    if drive == "D:" or resolved == project or resolved.startswith(project + os.sep):
        return resolved
    raise ConfigError(
        f"path is outside D: and the project dir: {resolved}")
