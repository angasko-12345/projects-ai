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


class TestActionMapping(unittest.TestCase):
    """Policy index -> GameInterface -> ActionMapper -> backend. No SendInput here."""

    def _game(self, table, frames=None):
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionMapper, RecordingBackend

        backend = RecordingBackend()
        frames = frames or [_frame(10), _frame(20)]
        capture = ScreenCapture(SyntheticBackend(frames), 0, 0, 64, 48, 64, 48)
        return GameInterface(capture, ActionMapper(backend, table)), backend

    def test_custom_table_without_rl_changes(self):
        from interface.controller import ActionDef

        game, backend = self._game([ActionDef("NOOP"),
                                    ActionDef("JUMP", kind="key", vk=0x20, hold_ms=0)])
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None)
        self.assertEqual(env.action_space.n, 2)
        env.reset(seed=0)
        env.step(1)
        self.assertEqual(backend.calls, [("key_down", 0x20), ("key_up", 0x20)])
        env.step(0)
        self.assertEqual(len(backend.calls), 2)  # NOOP emits nothing

    def test_default_table_index_routes_to_key(self):
        from interface.controller import VK, pc_action_table

        game, backend = self._game(pc_action_table(hold_ms=0))
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None)
        self.assertEqual(env.action_space.n, 10)
        env.reset(seed=0)
        names = [a.name for a in game.controller.table]
        env.step(names.index("PRESS_W"))
        self.assertEqual(backend.calls, [("key_down", VK["W"]), ("key_up", VK["W"])])

    def test_hold_sleeps_exactly_once(self):
        from unittest.mock import patch

        from interface.controller import ActionDef

        game, _ = self._game([ActionDef("HIT", kind="key", vk=0x57, hold_ms=50)])
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None, post_action_delay_ms=0.0)
        env.reset(seed=0)
        with patch("time.sleep") as asleep:
            env.step(0)
        self.assertEqual([c.args[0] for c in asleep.call_args_list], [0.05])

    def test_post_action_delay_sleeps_once(self):
        from unittest.mock import patch

        from interface.controller import ActionDef

        game, _ = self._game([ActionDef("NOOP")])
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None, post_action_delay_ms=25.0)
        env.reset(seed=0)
        with patch("time.sleep") as asleep:
            env.step(0)
        self.assertEqual([c.args[0] for c in asleep.call_args_list], [0.025])

    def test_no_hidden_delays_by_default(self):
        from unittest.mock import patch

        from interface.controller import ActionDef

        game, _ = self._game([ActionDef("NOOP")])
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None)
        env.reset(seed=0)
        with patch("time.sleep") as asleep:
            env.step(0)
        asleep.assert_not_called()

    def test_negative_delay_rejected(self):
        from interface.controller import ActionDef

        game, _ = self._game([ActionDef("NOOP")])
        with self.assertRaises(ValueError):
            ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                            post_action_delay_ms=-1.0)


class FakeClock:
    def __init__(self):
        self.now_t = 100.0
        self.sleeps = []

    def now(self):
        return self.now_t

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def advance(self, seconds):
        self.now_t += seconds


class TestTimingAndLifecycle(unittest.TestCase):
    def _game(self):
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture, SyntheticBackend
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        capture = ScreenCapture(SyntheticBackend([_frame(10), _frame(20)]), 0, 0, 64, 48, 64, 48)
        table = [ActionDef("NOOP", hold_ms=0), ActionDef("GO", kind="key", vk=0x57, hold_ms=0)]
        return GameInterface(capture, ActionMapper(RecordingBackend(), table))

    def _env(self, clock=None, **overrides):
        args = {"interface": self._game(),
                "reward_provider": NullReward(),
                "termination_provider": StepLimitTermination(max_steps=1000),
                "lifecycle": FakeLifecycle(),
                "clock": clock or FakeClock()}
        args.update(overrides)
        return ExternalGameEnv(**args)

    def test_normal_step_no_timeouts(self):
        env = self._env()
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step(0)
        self.assertEqual((terminated, truncated), (False, False))

    def test_startup_once_reset_every_time(self):
        clock = FakeClock()
        env = self._env(clock, lifecycle=FakeLifecycle(available=False),
                        startup_delay_ms=100.0, reset_delay_ms=50.0)
        env.reset(seed=0)  # attach runs -> startup + reset sleeps
        env.reset(seed=1)  # attached already -> reset sleep only
        self.assertEqual(clock.sleeps, [0.1, 0.05, 0.05])

    def test_timeout_by_steps(self):
        env = self._env(max_episode_steps=2)
        env.reset(seed=0)
        _, _, term1, trunc1, _ = env.step(0)
        _, _, term2, trunc2, _ = env.step(0)
        self.assertEqual((term1, trunc1), (False, False))
        self.assertEqual((term2, trunc2), (False, True))

    def test_timeout_by_duration(self):
        clock = FakeClock()
        env = self._env(clock, max_episode_seconds=10.0)
        env.reset(seed=0)
        clock.advance(5.0)
        _, _, _, trunc1, _ = env.step(0)
        clock.advance(6.0)
        _, _, term2, trunc2, _ = env.step(0)
        self.assertFalse(trunc1)
        self.assertFalse(term2)
        self.assertTrue(trunc2)

    def test_timeout_never_reports_termination(self):
        env = self._env(termination_provider=ScriptedTermination([(False, False)] * 5),
                        max_episode_steps=1)
        env.reset(seed=0)
        _, _, terminated, truncated, _ = env.step(0)
        self.assertFalse(terminated)
        self.assertTrue(truncated)

    def test_reset_clears_state(self):
        env = self._env(termination_provider=StepLimitTermination(max_steps=2))
        env.reset(seed=0)
        env.step(0)
        env.reset(seed=1)  # step counter cleared: next step is step 1 again
        _, _, _, trunc, _ = env.step(0)
        self.assertFalse(trunc)
        env.reset(seed=2)  # repeated resets stay valid
        obs, info = env.reset(seed=3)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(info, {})

    def test_lost_window_mid_episode_raises(self):
        from interface.adapter import GameInterface
        from interface.capture import ScreenCapture
        from interface.controller import ActionDef, ActionMapper, RecordingBackend

        class DyingBackend:
            def __init__(self):
                self.calls = 0

            def grab(self, bbox):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("window gone")
                return _frame(10)

        capture = ScreenCapture(DyingBackend(), 0, 0, 64, 48, 64, 48)
        game = GameInterface(capture, ActionMapper(RecordingBackend(), [ActionDef("NOOP", hold_ms=0)]))
        env = ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                              lifecycle=None, clock=FakeClock())
        env.reset(seed=0)
        with self.assertRaises(RuntimeError):
            env.step(0)

    def test_lifecycle_failure_and_restart(self):
        from interface.window import WindowNotFoundError

        class FlakyManager:
            def __init__(self):
                self.attaches = 0
                self.restarts = []
                self.live = False

            def attach(self):
                self.attaches += 1
                if not self.live:
                    raise WindowNotFoundError("not yet")

            def focus(self):
                return True

            def is_alive(self):
                return self.live

            def detach(self):
                self.live = False

            def restart(self, command):
                self.restarts.append(command)
                self.live = True

        from environment.external_game import WindowLifecycle

        lifecycle = WindowLifecycle(FlakyManager(), launch_command=["game.exe"])
        self.assertFalse(lifecycle.is_available())
        self.assertTrue(lifecycle.attach())  # attach fails -> restart -> reattach works
        self.assertTrue(lifecycle.is_available())
        self.assertTrue(lifecycle.reset_session())

    def test_bad_timing_config_rejected(self):
        game = self._game()
        with self.assertRaises(ValueError):
            ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                            startup_delay_ms=-1.0)
        with self.assertRaises(ValueError):
            ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                            max_episode_steps=0)
        with self.assertRaises(ValueError):
            ExternalGameEnv(game, NullReward(), StepLimitTermination(max_steps=10),
                            max_episode_seconds=-2.0)


class TestExternalFactory(unittest.TestCase):
    def _synthetic_cfg(self, **overrides):
        cfg = {"type": "external",
               "num_stack": 4, "obs_size": 84,
               "capture": {"mode": "synthetic", "out_width": 64, "out_height": 48,
                           "frame_value": 100},
               "actions": {"backend": "recording", "table": "default"},
               "timing": {},
               "reward": {"provider": "null"},
               "termination": {"provider": "step_limit", "max_steps": 4}}
        cfg.update(overrides)
        return cfg

    def test_full_flow_to_model_obs(self):
        import importlib.util

        if importlib.util.find_spec("torch") is None:
            self.skipTest("torch not installed")
        import torch
        from agent.model import ActorCritic
        from environment.external_game import make_external_env_from_config

        env = make_external_env_from_config(self._synthetic_cfg())()
        obs, info = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
        self.assertEqual(obs.dtype, np.float32)
        self.assertEqual(info, {})
        model = ActorCritic(num_actions=int(env.action_space.n)).eval()
        with torch.no_grad():
            logits, value, _ = model(torch.from_numpy(obs).unsqueeze(0),
                                     model.initial_state(1))
        self.assertEqual(tuple(logits.shape), (1, int(env.action_space.n)))
        self.assertEqual(tuple(value.shape), (1,))
        acted, total = [], 0.0
        while True:
            obs, reward, terminated, truncated, _ = env.step(0)
            acted.append(True)
            total += reward
            self.assertEqual(obs.shape, (4, 84, 84))
            if terminated or truncated:
                break
            self.assertLess(len(acted), 10)
        self.assertEqual(len(acted), 4)  # step_limit max_steps
        self.assertEqual(total, 0.0)  # null reward throughout

    def test_toy_default_unchanged(self):
        from training.experiment import make_env_from_config

        env = make_env_from_config({})()
        obs, _ = env.reset(seed=0)
        self.assertEqual(obs.shape, (4, 84, 84))
        type_name = type(env).__name__
        self.assertEqual(type_name, "PreprocessingWrapper")

    def test_safeguard_blocks_live_capture(self):
        from environment.external_game import make_external_env_from_config

        cfg = self._synthetic_cfg(capture={"mode": "region", "out_width": 32,
                                           "out_height": 32})
        with self.assertRaises(RuntimeError):
            make_external_env_from_config(cfg)
        cfg["allow_live_capture"] = True
        env = make_external_env_from_config(cfg)()  # builds; captures nothing
        self.assertEqual(tuple(env.observation_space.shape), (4, 84, 84))

    def test_window_needs_title(self):
        from environment.external_game import make_external_env_from_config

        cfg = self._synthetic_cfg(capture={"mode": "window", "out_width": 32,
                                           "out_height": 32},
                                  allow_live_capture=True)
        with self.assertRaises(ValueError):
            make_external_env_from_config(cfg)()

    def test_custom_action_table_from_config(self):
        from environment.external_game import make_external_env_from_config

        table = [{"name": "NOOP", "kind": "noop"},
                 {"name": "FIRE", "kind": "key", "vk": 32, "hold_ms": 0}]
        cfg = self._synthetic_cfg(actions={"backend": "recording", "table": table})
        env = make_external_env_from_config(cfg)()
        self.assertEqual(env.action_space.n, 2)
        env.reset(seed=0)
        env.step(1)
        with self.assertRaises(ValueError):
            env.step(2)
        with self.assertRaises(ValueError):
            make_external_env_from_config(
                self._synthetic_cfg(actions={"backend": "smoke-signals"}))()
        with self.assertRaises(ValueError):
            make_external_env_from_config(self._synthetic_cfg(capture={"mode": "lidar"}))
        with self.assertRaises(ValueError):
            make_external_env_from_config(
                self._synthetic_cfg(reward={"provider": "score-reader"}))
        with self.assertRaises(ValueError):
            make_external_env_from_config(
                self._synthetic_cfg(termination={"provider": "vibes"}))

    def test_cli_trains_external_synthetic(self):
        import importlib.util
        import tempfile
        from pathlib import Path

        if importlib.util.find_spec("torch") is None:
            self.skipTest("torch not installed")
        import yaml

        import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._synthetic_cfg()
            cfg["termination"] = {"provider": "step_limit", "max_steps": 8}
            cfg["timing"] = {}
            full = {
                "env": cfg,
                "model": {"in_channels": 4, "frame_size": 84, "feature_dim": 32,
                          "hidden_size": 16, "num_layers": 1},
                "ppo": {"total_timesteps": 8, "rollout_length": 8, "minibatch_size": 8,
                        "update_epochs": 1, "learning_rate": 1e-3, "seed": 0,
                        "checkpoint_dir": str(Path(tmp) / "ckpt"),
                        "checkpoint_every_updates": 100},
                "eval": {"episodes": 1},
            }
            cfg_path = str(Path(tmp) / "ext.yaml")
            Path(cfg_path).write_text(yaml.safe_dump(full), encoding="utf-8")
            self.assertEqual(cli_main.main(["train", "--config", cfg_path]), 0)
            self.assertTrue((Path(tmp) / "ckpt" / "ppo_final.pt").exists())


class AdvancingClock(FakeClock):
    """FakeClock whose sleep advances time (for settle-poll tests)."""

    def sleep(self, seconds):
        super().sleep(seconds)
        self.advance(seconds)


def _banner_frame():
    return np.full((48, 64, 3), (255, 0, 0), dtype=np.uint8)


class TestResetSettle(unittest.TestCase):
    def _env(self, frames, provider, clock=None, **overrides):
        from environment.external_game import ExternalGameEnv

        args = {"lifecycle": None, "clock": clock or AdvancingClock()}
        args.update(overrides)
        return ExternalGameEnv(FakeInterface(frames), NullReward(), provider, **args)

    def test_settle_skips_banner(self):
        from environment.extern_pong_rewards import ExternPongTermination, red_mask

        plain = _frame(0)
        env = self._env([_banner_frame(), _banner_frame(), plain],
                        ExternPongTermination(), reset_settle_timeout_s=10.0)
        env.reset(seed=0)
        self.assertEqual(int(red_mask(env.render()).sum()), 0)  # settled on live play
        self.assertEqual(env.interface.captures, 3)

    def test_settle_timeout_best_effort(self):
        from environment.extern_pong_rewards import ExternPongTermination, red_mask

        env = self._env([_banner_frame()], ExternPongTermination(),
                        reset_settle_timeout_s=0.12)
        env.reset(seed=0)  # banner never clears: proceeds anyway, no hang
        self.assertGreater(int(red_mask(env.render()).sum()), 300)
        self.assertGreater(env.interface.captures, 1)

    def test_settle_disabled(self):
        from environment.extern_pong_rewards import ExternPongTermination, red_mask

        for timeout in (None, 0.0):
            env = self._env([_banner_frame(), _frame(0)], ExternPongTermination(),
                            reset_settle_timeout_s=timeout)
            env.reset(seed=0)
            self.assertGreater(int(red_mask(env.render()).sum()), 300)
            self.assertEqual(env.interface.captures, 1)

    def test_settle_skipped_without_frame_signal(self):
        env = self._env([_banner_frame(), _frame(0)], StepLimitTermination(max_steps=10),
                        reset_settle_timeout_s=10.0)
        env.reset(seed=0)  # step limits have no per-frame signal: single capture
        self.assertEqual(env.interface.captures, 1)

    def test_negative_settle_timeout_rejected(self):
        from environment.external_game import ExternalGameEnv

        with self.assertRaises(ValueError):
            ExternalGameEnv(FakeInterface([_frame(0)]), NullReward(),
                            StepLimitTermination(max_steps=10),
                            reset_settle_timeout_s=-1.0)
