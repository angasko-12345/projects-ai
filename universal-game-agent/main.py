"""Universal Game Agent — sanity-check entry point.

Loads configs/default.yaml, sets up logging, reports ML dependency status.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def check_dependencies() -> dict[str, str]:
    status = {}
    for mod, label in (
        ("torch", "torch"),
        ("gymnasium", "gymnasium"),
        ("mss", "mss"),
        ("yaml", "pyyaml"),
    ):
        spec = importlib.util.find_spec(mod)
        if spec is None:
            status[label] = "missing"
            continue
        try:
            m = importlib.import_module(mod)
            status[label] = getattr(m, "__version__", "installed")
        except Exception as exc:  # pragma: no cover - defensive
            status[label] = f"import-failed: {exc}"
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Universal Game Agent sanity check")
    parser.add_argument("--config", default=str(ROOT / "configs" / "default.yaml"))
    parser.add_argument("--log-level", default=None, help="override logging.level from config")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Config: tolerated as missing until `pip install -r requirements.txt`.
    try:
        from configs import load_config

        config = load_config(args.config)
    except RuntimeError as exc:
        print(f"config unavailable: {exc}")
        config = {"logging": {"level": "INFO", "dir": "logs"}}

    log_level = args.log_level or config.get("logging", {}).get("level", "INFO")
    log_dir = config.get("logging", {}).get("dir", "logs")
    from training.logger import setup_logging

    log = setup_logging(log_dir=ROOT / log_dir, level=log_level)

    deps = check_dependencies()
    log.info("universal-game-agent starting")
    log.info("config: %s", args.config)
    for name, version in deps.items():
        log.info("dependency %-18s %s", name, version)
    missing = [k for k, v in deps.items() if v == "missing"]
    if missing:
        log.warning("missing dependencies: %s (pip install -r requirements.txt)", ", ".join(missing))
    log.info("config and logging OK; see README for training/eval entry points.")
    print("universal-game-agent OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
