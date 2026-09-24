"""Contract tests for the external-game boundary (fakes only, no OS side effects)."""
import unittest

import numpy as np

from environment.external_game import (
    ExternalGameEnv,
    GameLifecycle,
    NullReward,
    RewardProvider,
    StepLimitTermination,
    TerminationProvider,
    WindowLifecycle,
)


def _frame(value=0, h=48, w=64):
    return np.full((h, w, 3), value, dtype=np.uint8)


class FakeInterface:
    """Records execute calls, serves scripted frames. No OS touched."""

    def __init__(self, frames, num_actions=3):
        self.frames = list(frames)
        self.executed = []
        self.captures = 0
        self._num_actions = num_actions

    @property
    def num_actions(self):
        return self._num_actions

    def capture(self):
        frame = self.frames[min(self.captures, len(self.frames) - 1)]
        self.captures += 1
        return frame.copy()

    def execute(self, action):
        if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < self._num_actions:
            raise ValueError(f"invalid action {action!r}")
        self.executed.append(action)
        return f"ACTION_{action}"


class FakeLifecycle(GameLifecycle):
    def __init__(self, available=True, bindable=True):
        self.available = available
        self.bindable = bindable
        self.attached = False
        self.focus_calls = 0
        self.closed = False

    def attach(self):
        if not self.bindable:
            return False
        self.attached = True
        self.available = True  # successful bind makes the session available
        return True

    def focus(self):
        self.focus_calls += 1
        return self.available

    def is_available(self):
        return self.available

    def reset_session(self):
        return False

    def close(self):
        self.closed = True


class FixedReward(RewardProvider):
    def __init__(self, value=1.0):
        self.value = value
        self.calls = []

    def reward(self, previous_pixels, pixels, action):
        assert previous_pixels.shape == pixels.shape == (48, 64, 3)
        self.calls.append((previous_pixels.copy(), pixels.copy(), action))
        return self.value


class ScriptedTermination(TerminationProvider):
    def __init__(self, flags):
        self.flags = list(flags)

    def done(self, previous_pixels, pixels, action, steps):
        assert steps >= 1
        return self.flags[min(steps - 1, len(self.flags) - 1)]


def _env(**overrides):
    args = {
        "interface": FakeInterface([_frame(10), _frame(20), _frame(30)]),
        "reward_provider": FixedReward(1.0),
        "termination_provider": ScriptedTermination([(False, False)] * 10),
        "lifecycle": FakeLifecycle(),
    }
    args.update(overrides)
    return ExternalGameEnv(**args)


class TestExternalGameContract(unittest.TestCase):
    def test_reset_produces_model_obs(self):
        env = _env()
        obs, info = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertEqual(info, {})

    def test_step_delegates_action_and_capture(self):
        interface = FakeInterface([_frame(10), _frame(20)])
        env = _env(interface=interface)
        env.reset()
        captures_before = interface.captures
        obs, reward, terminated, truncated, info = env.step(2)
        self.assertEqual(interface.executed, [2])
        self.assertEqual(interface.captures, captures_before + 1)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(reward, 1.0)
        self.assertEqual((terminated, truncated), (False, False))
        self.assertEqual(info, {})

    def test_observation_is_pixels_only(self):
        interface = FakeInterface([_frame(200)])
        env = _env(interface=interface)
        obs, info = env.reset()
        _, info2 = env.step(0), env.step(0)[4]
        self.assertIsInstance(obs, np.ndarray)
        self.assertEqual(info, {})
        self.assertEqual(info2, {})
        self.assertNotIsInstance(obs, dict)

    def test_lifecycle_separate_from_reward_termination(self):
        lifecycle, reward, term = FakeLifecycle(available=False), FixedReward(2.0), ScriptedTermination([(True, False)])
        env = _env(lifecycle=lifecycle, reward_provider=reward, termination_provider=term)
        env.reset()
        self.assertTrue(lifecycle.attached)
        _, r, terminated, _, _ = env.step(1)
        self.assertTrue(terminated)
        self.assertEqual(r, 2.0)
        # Swap providers without touching lifecycle or interface.
        env.reward_provider = NullReward()
        env.termination_provider = StepLimitTermination(max_steps=1000)
        env.reset()
        _, r2, term2, trunc2, _ = env.step(1)
        self.assertEqual(r2, 0.0)
        self.assertEqual((term2, trunc2), (False, False))

    def test_step_limit_truncates(self):
        env = _env(termination_provider=StepLimitTermination(max_steps=2), lifecycle=None)
        env.reset()
        _, _, _, trunc1, _ = env.step(0)
        _, _, _, trunc2, _ = env.step(0)
        self.assertFalse(trunc1)
        self.assertTrue(trunc2)

    def test_invalid_actions_fail_cleanly(self):
        env = _env()
        env.reset()
        for bad in (-1, 3, None, "1", 1.5, True):
            with self.assertRaises((ValueError, TypeError), msg=f"action={bad!r}"):
                env.step(bad)

    def test_step_before_reset_fails(self):
        with self.assertRaises(RuntimeError):
            _env().step(0)

    def test_dead_lifecycle_fails_fast(self):
        env = _env(lifecycle=FakeLifecycle(available=False, bindable=False))
        with self.assertRaises(RuntimeError):
            env.reset()

    def test_spaces_match_contract(self):
        env = _env()
        self.assertEqual(env.action_space.n, 3)
        self.assertEqual(tuple(env.observation_space.shape), (4, 84, 84))

    def test_window_lifecycle_adapts_manager(self):
        class FakeManager:
            def __init__(self):
                self.attached_calls = 0

            def attach(self):
                self.attached_calls += 1

            def focus(self):
                return True

            def is_alive(self):
                return True

            def detach(self):
                self.attached_calls = -1

            def restart(self, command):
                raise AssertionError("no relaunch configured")

        manager = FakeManager()
        lifecycle = WindowLifecycle(manager)
        self.assertTrue(lifecycle.attach())
        self.assertTrue(lifecycle.focus())
        self.assertTrue(lifecycle.is_available())
        self.assertFalse(lifecycle.reset_session())  # no launch command -> unsupported
        lifecycle.close()
        self.assertIsInstance(lifecycle, GameLifecycle)


if __name__ == "__main__":
    unittest.main()


class TestCapturePipeline(unittest.TestCase):
    """Fake RGB -> real GameInterface -> ExternalGameEnv -> preprocessing."""

    def _game(self, frames):
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        backend = RecordingBackend()
        capture = ScreenCapture(SyntheticBackend(frames), 0, 0, 64, 48, 64, 48)
        table = [ActionDef("NOOP"),
                 ActionDef("PRESS_W", kind="key", vk=0x57, hold_ms=0),
                 ActionDef("LOOK", kind="mouse_move", dx=5, dy=0)]
        game = GameInterface(capture, ActionMapper(backend, table))
        return game, backend

    def test_pipeline_produces_model_obs(self):
        from environment.preprocessing import preprocess_frame

        frames = [_frame(30), _frame(90), _frame(150)]
        game, backend = self._game(frames)
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None)
        obs, info = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertEqual(info, {})
        # All four channels equal the processed first frame right after reset.
        expected = preprocess_frame(frames[0])
        for ch in range(4):
            self.assertTrue(np.allclose(obs[ch], expected, atol=1e-6))
        obs, reward, _, _, info = env.step(1)
        self.assertEqual(backend.calls, [("key_down", 0x57), ("key_up", 0x57)])
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(info, {})

    def test_stack_ordering_across_frames(self):
        from environment.preprocessing import preprocess_frame

        frames = [_frame(v) for v in (10, 40, 90, 160, 220)]
        game, _ = self._game(frames)
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None)
        env.reset(seed=0)  # stack filled with frames[0]
        env.step(0)  # sees frames[1]
        obs, _, _, _, _ = env.step(0)  # sees frames[2]
        # Newest frame last; older frames shift toward the front.
        for i, frame in enumerate((frames[0], frames[0], frames[1], frames[2])):
            self.assertTrue(np.allclose(obs[i], preprocess_frame(frame), atol=1e-6),
                            f"channel {i}")
        self.assertGreater(obs[-1].mean(), obs[0].mean())
