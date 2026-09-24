"""Logging infrastructure: console + file logging under logs/."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_dir: str | Path = "logs", level: str = "INFO") -> logging.Logger:
    """Configure root logger once; return the project logger."""
    upper = str(level).upper()
    if upper not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"unknown logging level {level!r}: expected DEBUG/INFO/WARNING/ERROR/CRITICAL")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("universal_game_agent")
    logger.setLevel(getattr(logging, upper))
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logfile = logging.FileHandler(Path(log_dir) / "universal-game-agent.log", encoding="utf-8")
    logfile.setFormatter(fmt)
    logger.addHandler(console)
    logger.addHandler(logfile)
    logger.propagate = False
    return logger
