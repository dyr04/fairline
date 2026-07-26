"""Canonical book/team ids across providers (plan §7 T13, §3 reconciliation rule).

Fragmented ids ('Pinnacle' vs 'pinnacle') would silently split the sharpness
table. This module is where every provider's names get mapped INTO The Odds
API's key space, which is our canonical space."""
from __future__ import annotations

# Known cross-provider book aliases. Left = source key, right = canonical.
# SGO's keys mostly match The Odds API already; add here only when they diverge.
_BOOK_ALIASES = {
    "williamhill": "williamhill_us",   # SGO uses williamhill; TOA uses williamhill_us
    "espnbet": "espnbet",              # both align
    "pointsbet": "pointsbetus",        # SGO drops the _us suffix
}

# Team-name aliases — grow when we spot mismatches in verify_t13.
_TEAM_ALIASES: dict[str, str] = {}


def canonical_book(book_key: str) -> str:
    k = book_key.strip().lower()
    return _BOOK_ALIASES.get(k, k)


def canonical_team(name: str) -> str:
    n = " ".join(name.strip().split())
    return _TEAM_ALIASES.get(n, n)


def american_to_decimal(american: str | int | float) -> float:
    """Convert '+150' or '-120' style American odds to decimal (e.g. 2.50, 1.833)."""
    s = str(american).strip().replace("+", "")
    a = float(s)
    if a >= 100:
        return round(1.0 + a / 100.0, 4)
    if a <= -100:
        return round(1.0 + 100.0 / abs(a), 4)
    raise ValueError(f"invalid American odds: {american!r}")