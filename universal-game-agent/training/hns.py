"""Human-normalised Atari scores. Stdlib only.

Reference scores (random, human) are copied verbatim from DeepMind's DQN Zoo,
``dqn_zoo/atari_data.py`` (Apache-2.0):
https://github.com/google-deepmind/dqn_zoo/blob/master/dqn_zoo/atari_data.py
(fetched 2026-10-26). Their protocol: each episode starts with 1..30 uniformly
random no-ops, episodes capped at 108,000 frames (30 min), "human" = a
professional game tester. HNS = (score - random) / (human - random): 0 = random,
1 = human.

UGA's protocol differs in places (sticky actions 0.25 in the Stage 2 benchmark,
a 27,000-frame eval cap, the 18-action pad), so UGA HNS values are comparable
across UGA arms and seeds, and only *indicative* against published HNS. Every
report that prints HNS says so. Only the games UGA's suites use are listed; add
more from the same source, never from memory.
"""
from __future__ import annotations

import math

SOURCE_URL = "https://github.com/google-deepmind/dqn_zoo/blob/master/dqn_zoo/atari_data.py"

#: game -> (random score, human score), ALE rom ids as used by ale-py.
REFERENCE_SCORES: dict[str, tuple[float, float]] = {
    "asterix": (210.0, 8503.3),
    "beam_rider": (363.9, 16926.5),
    "breakout": (1.7, 30.5),
    "demon_attack": (152.1, 1971.0),
    "freeway": (0.0, 29.6),
    "pong": (-20.7, 14.6),
    "qbert": (163.9, 13455.0),
    "seaquest": (68.4, 42054.7),
    "space_invaders": (148.0, 1668.7),
}


def has_reference(game: str) -> bool:
    return game in REFERENCE_SCORES


def human_normalized(game: str, score: float) -> float:
    """``(score - random) / (human - random)``; NaN for a game without a reference."""
    if game not in REFERENCE_SCORES:
        return math.nan
    random, human = REFERENCE_SCORES[game]
    return (float(score) - random) / (human - random)


def _median(values):
    vals = sorted(values)
    if not vals:
        return math.nan
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def aggregate(scores: dict[str, float]) -> dict:
    """Per-game HNS plus mean / median over the games that have a reference."""
    per_game = {g: human_normalized(g, s) for g, s in scores.items()}
    known = [v for g, v in per_game.items() if has_reference(g)]
    return {"per_game": per_game,
            "mean": sum(known) / len(known) if known else math.nan,
            "median": _median(known), "games": len(known), "source": SOURCE_URL}
