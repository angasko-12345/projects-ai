"""Vector-environment containers. Stdlib only.

:class:`SyncVectorEnv` runs several Gymnasium-style environments in one
process, in order, with no autoreset and no multiprocessing.
:class:`AsyncVectorEnv` (opt-in, ``env.vector_backend: async``) runs the
same contract with one subprocess per slot; see its docstring.

Episode-reset policy (explicit, decided from the PPO rollout in
``training/ppo.py:collect_rollout`` and ``_collect_vector``):

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
  the vectorized PPO rollout (``training/ppo.py:_collect_vector``) keeps
  one hidden state per slot (carry vs. zeros at boundaries), which is
  exactly why autoreset must never happen here.

Still deferred: nothing on this container's side. PPO wiring is done
(``ppo.num_envs > 1`` training runs through
``training.experiment.make_env_for_training``); factories are still
expected to be homogeneous, and those spaces are only *reported*
(``single_*_space``), never verified here -- that caller contract is what
the trainer's slot stacking relies on.

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

    def __init__(self, factories, *, check_spaces: bool = False):
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
        if check_spaces:
            self._check_spaces()

    def _check_spaces(self):
        """Opt-in guard for heterogeneous (multi-game) slots.

        Different games may share one vector env only when every slot has the
        same action count and observation shape, because the trainer stacks
        slots into one batch and one policy head. Off by default, so existing
        callers keep the unverified homogeneous-factory contract unchanged.
        """
        def signature(env):
            act = getattr(getattr(env, "action_space", None), "n", None)
            obs = getattr(getattr(env, "observation_space", None), "shape", None)
            return act, tuple(obs) if obs is not None else None

        first = signature(self._envs[0])
        for index, env in enumerate(self._envs[1:], start=1):
            if signature(env) != first:
                self._closed = True
                for e in self._envs:
                    try:
                        e.close()
                    except Exception:
                        pass
                raise ValueError(
                    f"slot {index} has (action_n, obs_shape)={signature(env)}, "
                    f"slot 0 has {first}; multi-game slots must agree")

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
        for env, action in zip(self._envs, actions, strict=False):
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


# -- subprocess vector env ----------------------------------------------------
class SpaceStub:
    """Picklable stand-in for a space that cannot cross a process boundary.

    Keeps only what the trainer and ``check_spaces`` read: ``n`` and ``shape``.
    """

    def __init__(self, n=None, shape=None):
        self.n = n
        self.shape = shape

    def __repr__(self):
        return f"SpaceStub(n={self.n!r}, shape={self.shape!r})"


def _picklable_space(space):
    import pickle

    if space is None:
        return None
    try:
        pickle.dumps(space)
        return space
    except Exception:  # noqa: BLE001 - e.g. a class built with type() in a test
        shape = getattr(space, "shape", None)
        return SpaceStub(getattr(space, "n", None), tuple(shape) if shape is not None else None)


def _async_worker(conn, factory):
    """Child-process loop: one env, driven by ``(command, payload)`` messages.

    Every reply is ``("ok", value)`` or ``("error", (type name, message,
    traceback text))``; the parent re-raises errors. Nothing is reset unless
    the parent asks, so the no-autoreset contract is the wrapped env's own.
    """
    import traceback

    env = None
    try:
        try:
            env = factory()
            conn.send(("ok", {
                "action_space": _picklable_space(getattr(env, "action_space", None)),
                "observation_space": _picklable_space(getattr(env, "observation_space", None)),
                "uga_action_space": getattr(env, "uga_action_space", None),
            }))
        except Exception as exc:  # noqa: BLE001 - shipped to the parent
            conn.send(("error", (type(exc).__name__, str(exc), traceback.format_exc())))
            return
        while True:
            try:
                command, payload = conn.recv()
            except EOFError:
                return
            if command == "close":
                try:
                    env.close()
                    conn.send(("ok", None))
                except Exception as exc:  # noqa: BLE001
                    conn.send(("error", (type(exc).__name__, str(exc), traceback.format_exc())))
                env = None
                return
            try:
                if command == "step":
                    result = env.step(payload)
                elif command == "reset":
                    result = env.reset() if payload is None else env.reset(seed=payload)
                else:
                    raise ValueError(f"unknown command {command!r}")
                conn.send(("ok", result))
            except Exception as exc:  # noqa: BLE001
                conn.send(("error", (type(exc).__name__, str(exc), traceback.format_exc())))
    finally:
        if env is not None:
            try:
                env.close()
            except Exception:  # noqa: BLE001 - best effort on abnormal exit
                pass
        conn.close()


class AsyncVectorEnvError(RuntimeError):
    """An env raised inside an :class:`AsyncVectorEnv` worker process."""


class AsyncVectorEnv(SyncVectorEnv):
    """N environments, one subprocess each, same contract as :class:`SyncVectorEnv`.

    Opt-in (``env.vector_backend: async``). ``step`` sends every slot its
    action first and then collects the replies, so the envs step in
    parallel; everything else is the synchronous contract unchanged:
    per-slot ``reset_env``, no autoreset, terminated and truncated kept
    apart, values passed through untouched (observations keep their dtype).
    For the same factories, seeds and actions it returns exactly what
    :class:`SyncVectorEnv` returns (``tests/test_vec_async.py``).

    * Factories must be picklable: they cross a process boundary. Closures
      returned by ``make_env_from_config`` are not; use
      ``training.experiment.ConfigEnvFactory`` (what the ``async`` backend
      builds) or any module-level callable.
    * ``start_method`` defaults to ``"spawn"``, the only method Windows has,
      so Linux CI exercises the same path the user's PC runs. Spawned
      children re-import the factory's module, so scripts that build an
      ``AsyncVectorEnv`` at top level need the usual
      ``if __name__ == "__main__":`` guard.
    * An exception inside a worker is re-raised in the parent as
      :class:`AsyncVectorEnvError` carrying the original type, message and
      traceback text; the env must then be closed.
    * Observations travel through a pipe (pickled), so uint8 observations
      (``env.obs_dtype: uint8``) move a quarter of the bytes of float32.
    """

    def __init__(self, factories, *, check_spaces: bool = False, start_method: str = "spawn"):
        import multiprocessing as mp

        factories = list(factories)
        if not factories:
            raise ValueError("AsyncVectorEnv needs at least one environment factory")
        ctx = mp.get_context(start_method)
        self._envs = []  # no in-process envs; slots live in the workers
        self._closed = False
        self._conns = []
        self._procs = []
        self._meta = []
        try:
            for factory in factories:
                parent, child = ctx.Pipe()
                proc = ctx.Process(target=_async_worker, args=(child, factory), daemon=True)
                proc.start()
                child.close()
                self._conns.append(parent)
                self._procs.append(proc)
            for index in range(len(factories)):
                self._meta.append(self._recv(index))
        except BaseException:
            self._terminate()
            raise
        uga_space = self._meta[0].get("uga_action_space")
        if isinstance(uga_space, str) and uga_space:
            self.uga_action_space = uga_space
        if check_spaces:
            self._check_spaces()

    # -- plumbing ---------------------------------------------------------
    def _recv(self, index):
        try:
            status, value = self._conns[index].recv()
        except EOFError as exc:
            raise AsyncVectorEnvError(
                f"worker {index} exited without replying "
                f"(exit code {self._procs[index].exitcode})") from exc
        if status == "error":
            name, message, trace = value
            raise AsyncVectorEnvError(f"slot {index}: {name}: {message}\n{trace}")
        return value

    def _terminate(self):
        self._closed = True
        for conn in self._conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        for proc in self._procs:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5)

    def _check_spaces(self):
        def signature(meta):
            act = getattr(meta.get("action_space"), "n", None)
            obs = getattr(meta.get("observation_space"), "shape", None)
            return act, tuple(obs) if obs is not None else None

        first = signature(self._meta[0])
        for index, meta in enumerate(self._meta[1:], start=1):
            if signature(meta) != first:
                self._terminate()
                raise ValueError(
                    f"slot {index} has (action_n, obs_shape)={signature(meta)}, "
                    f"slot 0 has {first}; multi-game slots must agree")

    # -- SyncVectorEnv contract ------------------------------------------
    @property
    def num_envs(self) -> int:
        return len(self._conns)

    @property
    def single_action_space(self):
        return self._meta[0].get("action_space")

    @property
    def single_observation_space(self):
        return self._meta[0].get("observation_space")

    def reset(self, *, seeds=None):
        if seeds is not None and len(seeds) != self.num_envs:
            raise ValueError(f"need {self.num_envs} seeds, got {len(seeds)}")
        for index, conn in enumerate(self._conns):
            conn.send(("reset", None if seeds is None else seeds[index]))
        results = [self._recv(index) for index in range(self.num_envs)]
        return [r[0] for r in results], [r[1] for r in results]

    def step(self, actions):
        if len(actions) != self.num_envs:
            raise ValueError(f"need {self.num_envs} actions, got {len(actions)}")
        for conn, action in zip(self._conns, actions, strict=True):
            conn.send(("step", action))
        results = [self._recv(index) for index in range(self.num_envs)]
        observations, rewards, terminateds, truncateds, infos = [], [], [], [], []
        for obs, reward, terminated, truncated, info in results:
            observations.append(obs)
            rewards.append(reward)
            terminateds.append(terminated)
            truncateds.append(truncated)
            infos.append(info)
        return observations, rewards, terminateds, truncateds, infos

    def reset_env(self, index, *, seed=None):
        if not 0 <= index < self.num_envs:
            raise IndexError(
                f"env index out of range: {index!r} with {self.num_envs} envs")
        self._conns[index].send(("reset", seed))
        return self._recv(index)

    def close(self):
        """Close every worker's env once; repeat calls are no-ops.

        The first env error is re-raised after every worker has exited.
        """
        if self._closed:
            return
        first_error = None
        for index, conn in enumerate(self._conns):
            try:
                conn.send(("close", None))
                self._recv(index)
            except Exception as exc:  # noqa: BLE001 - re-raised below
                if first_error is None:
                    first_error = exc
        self._terminate()
        if first_error is not None:
            raise first_error

    def __del__(self):
        try:
            if not self._closed:
                self._terminate()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass
