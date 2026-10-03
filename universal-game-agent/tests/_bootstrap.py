"""Repo-root import bootstrap for test modules.

Every test module imports this first, via::

    try:
        from . import _bootstrap
    except ImportError:  # run as script or discovered top-level
        import _bootstrap

Under ``python -m unittest discover -s tests`` the repository root is already
on ``sys.path`` (the command runs from ``universal-game-agent/``), so this is a
no-op. Under direct execution (``python tests/<file>.py``) ``sys.path[0]`` is
``tests/`` and the root is missing, which used to break ``import main`` /
``from training...`` or silently turn every test into a skip. Inserting the
root here makes direct execution a valid convenience path without touching
production code or duplicating a path hack in 20 files. Idempotent.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
