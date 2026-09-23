"""Agent package: neural policy/value networks over pixel observations.

Consumes rendered frames (or preprocessed stacks) only; MUST NOT import
game-specific internal state.
"""
__all__ = ["ActorCritic"]


def __getattr__(name: str):
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
