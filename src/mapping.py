"""Implements plan §7 T13 (skeleton at T1): canonical book/team ids.

The Odds API's keys are the canonical space; this module is where any second
provider's names get mapped. Fragmented ids ('Pinnacle' vs 'pinnacle') would
silently split the sharpness table (§3 reconciliation rule)."""
from __future__ import annotations

_BOOK_ALIASES = {}   # e.g. {"pinnaclesports": "pinnacle"} — grows at T13
_TEAM_ALIASES = {}   # e.g. {"LA Sparks": "Los Angeles Sparks"} — grows at T13


def canonical_book(book_key: str) -> str:
    k = book_key.strip().lower()
    return _BOOK_ALIASES.get(k, k)


def canonical_team(name: str) -> str:
    n = " ".join(name.strip().split())
    return _TEAM_ALIASES.get(n, n)
