"""Implements plan §4.7 stake sizing: quarter-Kelly + human-unit rounding.

Why fractional Kelly: full Kelly is growth-optimal only if the win probability
is known exactly; under estimation error it overbets catastrophically, and the
Kelly curve is asymmetric (overbetting destroys growth faster than underbetting
sacrifices it). kelly_fraction=0.25 trades growth for drawdown control.

Why human rounding DOWN (floor to increment): calculator-exact stakes like
$98.34 are a book-profiling tell; flooring keeps the camouflage AND guarantees
the stake never exceeds computed Kelly (strictly under-Kelly, which trivially
satisfies the plan's <=105% bound). $98.34 @ $5 increment -> $95.
"""
from __future__ import annotations


def kelly_stake(fair_prob: float, price_decimal: float, bankroll: float,
                fraction: float = 0.25) -> float:
    """f* = (b*p - q)/b with b = price-1; returns dollar stake, floored at 0."""
    if not 0.0 < fair_prob < 1.0:
        raise ValueError("fair_prob must be in (0,1)")
    if price_decimal <= 1.0:
        raise ValueError("price must be > 1.0")
    b = price_decimal - 1.0
    f_star = (b * fair_prob - (1.0 - fair_prob)) / b
    return max(0.0, f_star * fraction * bankroll)


def human_round(stake: float, increment: float = 5.0) -> float:
    """Floor to the nearest increment multiple (never exceeds computed stake)."""
    if increment <= 0:
        raise ValueError("increment must be positive")
    return (stake // increment) * increment


def price_floor(fair_prob: float, min_edge: float) -> float:
    """Worst decimal price at which the bet still clears min_edge (plan §4.7).

    edge = fair_prob - implied_prob; implied at floor = fair_prob - min_edge.
    """
    implied = fair_prob - min_edge
    if implied <= 0:
        return float("inf")
    return 1.0 / implied


def edge_at(fair_prob: float, price_decimal: float) -> float:
    """Repricer core: edge of a live price against the frozen fair probability."""
    return fair_prob - 1.0 / price_decimal
