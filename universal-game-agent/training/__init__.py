"""Training package: PPO loop, logging helpers, checkpointing."""
__all__ = ["PPOTrainer", "PPOConfig", "compute_gae", "CuriosityModule", "CuriosityConfig"]

_PPO = {"PPOTrainer", "PPOConfig", "compute_gae"}


def __getattr__(name: str):
    if name in _PPO:
        from . import ppo

        return getattr(ppo, name)
    if name in __all__:
        from . import curiosity

        return getattr(curiosity, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
