"""Environment package: pixel-only toy environments for pipeline experiments.

The learning path exposes rendered frames only; ball/paddle coordinates,
velocities, scores, and collision flags are never in observations or info.
"""
__all__ = [
    "ToyPongEnv",
    "NOOP",
    "LEFT",
    "RIGHT",
    "FrameStack",
    "PreprocessingWrapper",
    "preprocess_frame",
]

_TOY_PONG = {"ToyPongEnv", "NOOP", "LEFT", "RIGHT"}


def __getattr__(name: str):
    # Lazy re-export: keeps `python -m environment.toy_pong` warning-free
    # (no submodule import at package init) while allowing
    # `from environment import ToyPongEnv`.
    if name in _TOY_PONG:
        from . import toy_pong

        return getattr(toy_pong, name)
    if name in __all__:
        from . import preprocessing

        return getattr(preprocessing, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
