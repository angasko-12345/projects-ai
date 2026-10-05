"""Minimal synchronous vector-environment container. Stdlib only.

Runs several Gymnasium-style environments in one process, in order, with
no autoreset and no multiprocessing.

Episode-reset policy (explicit, decided from the PPO rollout in
``training/ppo.py:collect_rollout``):

* Finished environments are NEVER reset automatically. PPO needs the
  terminal step's own observation, reward, and flags untouched: the
  truncation bootstrap reads V(final pre-reset obs, its hidden state),
  curiosity masks boundary steps via ``dones``, and GAE masks on
  ``terminated`` only. An autoreset that swapped in a fresh observation
  would corrupt all three, so the container must not do it.
* The only resets are :meth:`SyncVectorEnv.reset` (every environment) and
  :meth:`SyncVectorEnv.reset_env` (one finished slot). This mirrors the
  single-env loop, which resets exactly the finished episode inline while
  carrying everything else: full-reset-only would either destroy live
  episodes in other slots or leave done slots bricked.
* ``terminated`` and ``truncated`` are reported separately per slot and
  never conflated: termination means value 0, truncation means bootstrap
  continues, and both mean the caller restarts that slot from zeroed
  hidden state.
* Hidden state is entirely caller-side. This container is hidden-agnostic:
  a future vectorized rollout keeps one hidden state per slot (carry vs.
  zeros at boundaries), exactly as the single-env path does today.

Still deferred: the shared-space contract PPO will need, and PPO wiring
itself.

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

    def reset_env(self, index, *, seed=None):
        """Reset one slot (the vector analogue of the rollout's inline reset).

        Other slots are untouched. Returns ``(observation, info)``.
        Out-of-range indices raise ``IndexError``.
        """
        if not 0 <= index < len(self._envs):
            raise IndexError(
                f"env index out of range: {index!r} with {len(self._envs)} envs")
        if seed is None:
            return self._envs[index].reset()
        return self._envs[index].reset(seed=seed)


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
