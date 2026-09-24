"""Standalone Pong test game: own process, own window, pixels + keyboard only.

Run:  python games/extern_pong.py --title "TestPong" --seed 0
Debug: Left/Right or A/D move, P pause, R re-serve, Q/Escape quit.
Flags: --fps, --auto-quit SEC (self-close, for automated launch checks).

No sockets, no shared files, no state API -- the only way to observe or
drive this game is its window and the keyboard.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pong_logic import BALL_SIZE, HEIGHT, LEFT, NOOP, PADDLE_H, PADDLE_W, PADDLE_Y, RIGHT, WIDTH, PongLogic

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - tkinter ships with CPython
    tk = None


class PongApp:
    def __init__(self, root, logic: PongLogic, fps: int = 60, auto_quit: float = 0.0):
        self.root = root
        self.logic = logic
        self.delay_ms = max(1, 1000 // max(1, fps))
        self.stop_at = (time.monotonic() + auto_quit) if auto_quit > 0 else 0.0
        self.held = {LEFT: False, RIGHT: False}
        self.paused = False
        self.red_on = False  # hit signature: toggles each paddle hit, cleared on serve
        self.banner_until = 0.0
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg="black",
                                highlightthickness=0)
        self.canvas.pack()
        self.paddle = self.canvas.create_rectangle(0, 0, PADDLE_W, PADDLE_H, fill="white")
        self.ball = self.canvas.create_oval(0, 0, BALL_SIZE, BALL_SIZE, fill="white")
        self.score = self.canvas.create_text(8, 8, anchor="nw", fill="gray",
                                             text="", font=("TkDefaultFont", 9))
        self.banner = self.canvas.create_text(WIDTH // 2, HEIGHT // 2, fill="red",
                                              text="", font=("TkDefaultFont", 16))
        for key, action in (("<Left>", LEFT), ("<Right>", RIGHT), ("a", LEFT), ("d", RIGHT)):
            root.bind(key, lambda _e, a=action: self._hold(a, True))
            root.bind(f"<KeyRelease-{key.strip('<>')}>", lambda _e, a=action: self._hold(a, False))
        root.bind("p", lambda _e: self._toggle_pause())
        root.bind("r", lambda _e: self._reserve())
        root.bind("q", lambda _e: root.destroy())
        root.bind("<Escape>", lambda _e: root.destroy())
        self._tick()

    def _hold(self, action: int, down: bool) -> None:
        self.held[action] = down

    def _reserve(self) -> None:
        self.logic.re_serve()
        self.red_on = False

    def _toggle_pause(self) -> None:
        self.paused = not self.paused

    def _action(self) -> int:
        if self.held[LEFT] and not self.held[RIGHT]:
            return LEFT
        if self.held[RIGHT] and not self.held[LEFT]:
            return RIGHT
        return NOOP

    def _tick(self) -> None:
        if self.stop_at and time.monotonic() >= self.stop_at:
            self.root.destroy()
            return
        if not self.paused:
            event = self.logic.step(self._action())
            if event == "hit":
                self.red_on = not self.red_on
            elif event == "miss":
                self.banner_until = time.monotonic() + 1.0
        g = self.logic
        self.canvas.coords(self.paddle, g.paddle_x, PADDLE_Y,
                           g.paddle_x + PADDLE_W, PADDLE_Y + PADDLE_H)
        self.canvas.coords(self.ball, g.ball_x, g.ball_y,
                           g.ball_x + BALL_SIZE, g.ball_y + BALL_SIZE)
        self.canvas.itemconfig(self.ball, fill="red" if self.red_on else "white")
        self.canvas.itemconfig(self.score, text=f"hits {g.hits}  miss {g.misses}")
        self.canvas.itemconfig(self.banner,
                               text="MISS - press R" if time.monotonic() < self.banner_until else "")
        if g.over and time.monotonic() >= self.banner_until:
            self._reserve()  # terminal event shown, then visible reset/start state
        self.root.after(self.delay_ms, self._tick)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Pong test game")
    parser.add_argument("--title", default="ExternPong")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--auto-quit", type=float, default=0.0)
    args = parser.parse_args(argv)
    if tk is None:
        print("error: tkinter unavailable", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError:
        print("error: no display available", file=sys.stderr)
        return 2
    root.title(args.title)
    PongApp(root, PongLogic(seed=args.seed), fps=args.fps, auto_quit=args.auto_quit)
    try:
        root.mainloop()
    except tk.TclError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
