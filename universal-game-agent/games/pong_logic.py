"""Standalone Pong test-game simulation. Pure logic, no rendering, no I/O.

Independent from ``environment/toy_pong.py`` (different constants, fresh
code) so the external boundary can be tested honestly. The app layer
renders this state; nothing here is importable as agent input.
"""
from __future__ import annotations

import random

WIDTH, HEIGHT = 320, 240
PADDLE_W, PADDLE_H, PADDLE_Y, PADDLE_SPEED = 48, 8, HEIGHT - 16, 5
BALL_SIZE, BALL_SPEED = 6, 4

LEFT, NOOP, RIGHT = -1, 0, 1


class PongLogic:
    """Paddle (bottom) vs. falling ball. Step returns 'none' | 'hit' | 'miss'."""

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.paddle_x = (WIDTH - PADDLE_W) // 2
        self.hits = 0
        self.misses = 0
        self.over = False
        self.steps = 0
        self._serve()

    def _serve(self) -> None:
        self.ball_x = WIDTH // 2 + self._rng.randint(-40, 40)
        self.ball_y = HEIGHT // 4
        self.ball_vx = self._rng.choice((-BALL_SPEED, BALL_SPEED))
        self.ball_vy = BALL_SPEED  # always served downward
        self.over = False

    def re_serve(self) -> None:
        """Restart the ball after a miss, keeping scores."""
        self._serve()

    def step(self, action: int) -> str:
        """Advance one tick. Returns the visible event for this tick."""
        if action == LEFT:
            self.paddle_x = max(0, self.paddle_x - PADDLE_SPEED)
        elif action == RIGHT:
            self.paddle_x = min(WIDTH - PADDLE_W, self.paddle_x + PADDLE_SPEED)
        elif action != NOOP:
            raise ValueError(f"invalid action {action!r}: expected -1, 0, or 1")
        if self.over:
            return "none"
        self.ball_x += self.ball_vx
        self.ball_y += self.ball_vy
        if self.ball_x <= 0:
            self.ball_x, self.ball_vx = 0, BALL_SPEED
        elif self.ball_x >= WIDTH - BALL_SIZE:
            self.ball_x, self.ball_vx = WIDTH - BALL_SIZE, -BALL_SPEED
        if self.ball_y <= 0:
            self.ball_y, self.ball_vy = 0, BALL_SPEED
        event = "none"
        if self.ball_vy > 0 and self.ball_y + BALL_SIZE >= PADDLE_Y:
            if self.ball_x + BALL_SIZE > self.paddle_x and self.ball_x < self.paddle_x + PADDLE_W:
                self.ball_y, self.ball_vy = PADDLE_Y - BALL_SIZE, -BALL_SPEED
                self.hits += 1
                event = "hit"
        if self.ball_y >= HEIGHT:
            self.misses += 1
            self.over = True
            event = "miss"
        self.steps += 1
        return event
