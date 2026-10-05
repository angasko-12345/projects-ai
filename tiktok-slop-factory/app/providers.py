"""
Backend selection: one module, chosen explicitly.

The pipeline needs exactly four text/voice calls -- ``generate_ideas``,
``generate_script``, ``script_to_text``, ``generate_tts``, ``generate_caption``
-- plus ``get_gemini_api_key()`` for the config check. Two modules implement
that same contract:

    gemini  -> the Gemini API (needs GEMINI_API_KEY, the default)
    local   -> a local JSON file + Edge TTS (no key at all)

``resolve_backend(name)`` returns the module. Nothing is monkeypatched, so the
Gemini path is untouched by construction and a bad backend name fails at
startup rather than midway through a render.

This is deliberately a dict, not a plugin framework: two backends, one seam.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict

from .config import ConfigError, get_backend, normalize_backend

# Imported lazily inside resolve_backend() so a missing optional dependency
# (edge-tts, needed only by the local backend) cannot break the Gemini path.
_BACKEND_MODULES: Dict[str, str] = {
    "gemini": "app.gemini",
    "local": "app.local_text",
}

# The five calls the pipeline makes on a backend module. Both must provide
# them; this is the whole seam, and check_setup() is optional per backend.
CONTRACT = ("generate_ideas", "generate_script", "script_to_text",
            "generate_tts", "generate_caption")


def resolve_backend(name: str | None = None) -> ModuleType:
    """Return the provider module for ``name``.

    ``None`` means "ask the environment" (``TEXT_BACKEND``, default gemini).
    Raises ``ConfigError`` for an unknown name, an unimportable module, or a
    module missing part of the contract, so a typo is a one-line error instead
    of a broken run.
    """
    resolved = get_backend() if name is None else normalize_backend(name)
    module_name = _BACKEND_MODULES[resolved]
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ConfigError(
            f"Backend {resolved!r} could not be loaded from {module_name}: {e}. "
            "Install it with: pip install -r requirements.txt"
        ) from e

    missing = [fn for fn in CONTRACT if not callable(getattr(module, fn, None))]
    if missing:
        raise ConfigError(
            f"Backend {resolved!r} ({module_name}) is missing "
            f"{', '.join(missing)}. A backend must implement: "
            f"{', '.join(CONTRACT)}."
        )
    return module


def backend_name(module: ModuleType) -> str:
    """Reverse lookup, used by tests and error messages."""
    for name, module_name in _BACKEND_MODULES.items():
        if module_name == getattr(module, "__name__", ""):
            return name
    raise ConfigError(f"{module!r} is not a registered backend")