"""Tests for the standalone extern-Pong game mechanics + process launch."""
import subprocess
import sys
import unittest
from pathlib import Path

from games.pong_logic import HEIGHT, LEFT, NOOP, PADDLE_W, RIGHT, WIDTH, PongLogic

APP = Path(__file__).resolve().parent.parent / "games" / "extern_pong.py"


def _run(actions, seed=0, steps=200):
    game = PongLogic(seed=seed)
    events = []
    for i in range(steps):
        events.append(game.step(actions[i] if i < len(actions) else NOOP))
        if game.over:
            break
    return game, events


class TestPongLogic(unittest.TestCase):
    def test_paddle_stays_in_bounds(self):
        game = PongLogic(seed=0)
        for _ in range(200):
            game.step(LEFT)
        self.assertEqual(game.paddle_x, 0)
        for _ in range(200):
            game.step(RIGHT)
        self.assertEqual(game.paddle_x, WIDTH - PADDLE_W)

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            PongLogic(seed=0).step(7)

    def test_wall_bounce(self):
        game = PongLogic(seed=0)
        game.ball_x, game.ball_vx = 1, -4
        game.ball_y, game.ball_vy = 10, 4
        game.step(NOOP)
        self.assertEqual((game.ball_x, game.ball_vx), (0, 4))

    def test_hit_event_and_counter(self):
        game = PongLogic(seed=0)
        game.paddle_x = 100
        game.ball_x, game.ball_y, game.ball_vx, game.ball_vy = 110, 225, 0, 4
        self.assertEqual(game.step(NOOP), "hit")
        self.assertEqual(game.hits, 1)
        self.assertLess(game.ball_vy, 0)

    def test_miss_terminal_and_over(self):
        game = PongLogic(seed=0)
        game.paddle_x = 0
        game.ball_x, game.ball_y, game.ball_vx, game.ball_vy = 300, 236, 0, 4
        self.assertEqual(game.step(NOOP), "miss")
        self.assertTrue(game.over)
        self.assertEqual(game.misses, 1)
        before = (game.ball_x, game.ball_y)
        self.assertEqual(game.step(NOOP), "none")  # frozen until re-serve
        self.assertEqual((game.ball_x, game.ball_y), before)

    def test_reserve_keeps_scores(self):
        game, _ = _run([], seed=3)
        self.assertTrue(game.over)
        hits, misses = game.hits, game.misses
        game.re_serve()
        self.assertFalse(game.over)
        self.assertEqual((game.hits, game.misses), (hits, misses))

    def test_reset_clears(self):
        game, _ = _run([], seed=3)
        game.reset()
        self.assertEqual((game.hits, game.misses, game.steps), (0, 0, 0))
        self.assertFalse(game.over)

    def test_deterministic_same_seed(self):
        _, first = _run([LEFT, RIGHT, NOOP] * 30, seed=5)
        _, second = _run([LEFT, RIGHT, NOOP] * 30, seed=5)
        self.assertEqual(first, second)

    def test_hit_possible_with_tracking(self):
        # Naive tracking play survives well past the first serve.
        game = PongLogic(seed=1)
        for _ in range(500):
            action = LEFT if game.ball_x < game.paddle_x else (RIGHT if game.ball_x > game.paddle_x + PADDLE_W else NOOP)
            game.step(action)
            if game.over:
                game.re_serve()
        self.assertGreater(game.hits, 0)


class TestGameLaunch(unittest.TestCase):
    def test_launches_as_separate_process(self):
        try:
            proc = subprocess.Popen(
                [sys.executable, str(APP), "--title", "ExternPongTest",
                 "--seed", "0", "--auto-quit", "2"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self.skipTest(f"cannot spawn process: {exc}")
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("game process did not exit via --auto-quit")
        if proc.returncode == 2 and b"no display" in err:
            self.skipTest("no display available")
        self.assertEqual(proc.returncode, 0, err.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
