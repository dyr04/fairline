"""Implements plan §4.1: n-outcome vig removal, two methods.

Why two methods: the multiplicative (proportional) method assumes the book's
margin is spread proportionally to probability. Empirically books shade
longshots more (favorite-longshot bias), so proportional devig OVERSTATES
longshot fair probability. The power method corrects by solving for an
exponent k such that raw implied probabilities raised to k sum to 1, which
shifts more of the margin correction onto the longshots.
"""
from __future__ import annotations

from scipy.optimize import brentq


def _validate(odds: list[float]) -> None:
    if len(odds) < 2:
        raise ValueError("need >= 2 outcomes")
    if any(o <= 1.0 for o in odds):
        raise ValueError("all decimal odds must be > 1.0")


def overround(odds: list[float]) -> float:
    """Sum of raw implied probabilities minus 1 (the vig)."""
    _validate(odds)
    return sum(1.0 / o for o in odds) - 1.0


def devig_multiplicative(odds: list[float]) -> list[float]:
    """p_i = (1/o_i) / sum(1/o_j). Simple, standard, longshot-biased."""
    _validate(odds)
    raw = [1.0 / o for o in odds]
    s = sum(raw)
    return [p / s for p in raw]


def devig_power(odds: list[float]) -> list[float]:
    """Solve k with sum((1/o_i)^k) = 1, return (1/o_i)^k.

    k > 1 when overround > 0. brentq bracket [0.5, 3.0], widened once
    to [0.1, 10.0] if no sign change (plan §7 T3 step 3).
    """
    _validate(odds)
    raw = [1.0 / o for o in odds]

    def f(k: float) -> float:
        return sum(p**k for p in raw) - 1.0

    for lo, hi in ((0.5, 3.0), (0.1, 10.0)):
        if f(lo) * f(hi) < 0:
            k = brentq(f, lo, hi)
            return [p**k for p in raw]
    raise ValueError("power devig: no root in [0.1, 10.0]")
