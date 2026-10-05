"""Minimal synchronous vector-environment container. Stdlib only.

Runs several Gymnasium-style environments in one process, in order, with
no autoreset and no multiprocessing. PPO integration is deliberately not
decided here: this module only batches ``reset`` / ``step`` / ``close``.

Episode-reset policy (explicit): finished environments are NEVER reset
automatically. After an env reports ``terminated`` or ``truncated``, its
slot keeps returning that env's own post-done step results until the
caller resets. The only reset is :meth:`SyncVectorEnv.reset`, which resets
*every* environment. Selective per-env reset, autoreset modes, and the
shared-space contract PPO will need are deferred (see below).

Factory contract: every factory is a zero-argument callable producing an
object with ``reset(*, seed=None, options=None)``, ``step(action)``, and
``close()``. ``single_action_space`` / ``single_observation_space`` report
the *first* environment's spaces (``None`` when absent); factories are
expected to be homogeneous, but that sameness is a documented caller
contract, not something this container verifies.
"""
from __future__ import annotations


class SyncVectorEnv:
    """Step N independent environments in lockstep, same process."""

    def __init__(self, factories):
        factories = list(factories)
        if not factories:
            raise ValueError("SyncVectorEnv needs at least one environment factory")
        envs = []
        try:
            for make in factories:
                envs.append(make())
        except Exception:
            for env in envs:
                try:
                    env.close()
                except Exception:
                    pass
            raise
        self._envs = envs
        self._closed = False

    @property
    def num_envs(self) -> int:
        """Number of contained environments."""
        return len(self._envs)

    @property
    def single_action_space(self):
        """First env's action space (``None`` when it declares none)."""
        return getattr(self._envs[0], "action_space", None)

    @property
    def single_observation_space(self):
        """First env's observation space (``None`` when it declares none)."""
        return getattr(self._envs[0], "observation_space", None)

    def reset(self, *, seeds=None):
        """Reset every environment.

        ``seeds`` is ``None`` (plain ``reset()`` per env) or one seed per
        env. Returns ``(observations, infos)`` as two lists in env order;
        observations keep each env's native shape (no stacking).
        """
        if seeds is not None and len(seeds) != len(self._envs):
            raise ValueError(
                f"need {len(self._envs)} seeds, got {len(seeds)}")
        observations, infos = [], []
        for index, env in enumerate(self._envs):
            if seeds is None:
                obs, info = env.reset()
            else:
                obs, info = env.reset(seed=seeds[index])
            observations.append(obs)
            infos.append(info)
        return observations, infos

    def step(self, actions):
        """Run exactly one action per environment, in order.

        Returns ``(observations, rewards, terminateds, truncateds, infos)``
        as five per-env lists. Values pass through untouched: no coercion,
        no stacking, no autoreset.
        """
        if len(actions) != len(self._envs):
            raise ValueError(
                f"need {len(self._envs)} actions, got {len(actions)}")
        observations, rewards, terminateds, truncateds, infos = [], [], [], [], []
        for env, action in zip(self._envs, actions):
            obs, reward, terminated, truncated, info = env.step(action)
            observations.append(obs)
            rewards.append(reward)
            terminateds.append(terminated)
            truncateds.append(truncated)
            infos.append(info)
        return observations, rewards, terminateds, truncateds, infos

    def close(self):
        """Close every environment exactly once; repeat calls are no-ops.

        Every env is closed even when an earlier one raises; the first
        error is re-raised after the rest are done.
        """
        if self._closed:
            return
        self._closed = True
        first_error = None
        for env in self._envs:
            try:
                env.close()
            except Exception as exc:  # noqa: BLE001 - re-raised below
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
