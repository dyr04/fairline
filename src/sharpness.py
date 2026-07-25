"""Implements plan §4.5: learned book-sharpness consensus (Model B) + hybrid (H).

Pure functions first; the walk-forward driver guarantees weights at time t are
computed ONLY from games resolved before t (§9 P1 — the invariance test in
tests/test_sharpness.py is the proof). Exchanges enter via midpoint transform
upstream (signal_engine) and are scored here like any forecaster.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EWMA_LAMBDA = 0.98
SHRINK_N0 = 30.0


def brier(p: float, y: int) -> float:
    """Proper scoring rule: (p - y)^2, y in {0,1}. Lower = sharper."""
    if not 0.0 <= p <= 1.0 or y not in (0, 1):
        raise ValueError("p in [0,1], y in {0,1}")
    return (p - y) ** 2


def ewma_update(s: float | None, bs: float, lam: float = EWMA_LAMBDA) -> float:
    """S <- lam*S + (1-lam)*BS; first observation initializes at BS."""
    return bs if s is None else lam * s + (1.0 - lam) * bs


def shrink(s: float, n: float, s_bar: float, n0: float = SHRINK_N0) -> float:
    """Empirical-Bayes shrinkage toward the cross-book mean.

    Raw per-book Brier over <200 games is mostly noise; a new book enters at
    exactly the mean and earns deviation (plan §4.5 — same estimation-error
    logic as fractional Kelly)."""
    return (n * s + n0 * s_bar) / (n + n0)


def weights(shrunk_scores: dict[str, float]) -> dict[str, float]:
    """w_b proportional to 1/S_shrunk, normalized to sum to 1."""
    inv = {b: 1.0 / s for b, s in shrunk_scores.items() if s > 0}
    total = sum(inv.values())
    return {b: v / total for b, v in inv.items()}


def loo_consensus(probs: dict[str, float], w: dict[str, float],
                  exclude_book: str | None = None) -> float:
    """Leave-one-out weighted mean (§4.5 circularity fix): when scoring book X's
    edge, the consensus excludes X's own line."""
    items = [(b, p) for b, p in probs.items() if b != exclude_book and b in w]
    if not items:
        raise ValueError("no books left for consensus")
    total_w = sum(w[b] for b, _ in items)
    return sum(w[b] * p for b, p in items) / total_w


@dataclass
class SharpnessState:
    """Walk-forward state: per (book, sport) EWMA Brier and game counts."""
    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    counts: dict[tuple[str, str], float] = field(default_factory=dict)

    def shrunk(self, sport: str) -> dict[str, float]:
        keys = [k for k in self.scores if k[1] == sport]
        if not keys:
            return {}
        s_bar = sum(self.scores[k] for k in keys) / len(keys)
        return {k[0]: shrink(self.scores[k], self.counts[k], s_bar) for k in keys}

    def weights(self, sport: str) -> dict[str, float]:
        sh = self.shrunk(sport)
        return weights(sh) if sh else {}

    def update(self, book: str, sport: str, closing_prob: float, won: int) -> None:
        """Call ONLY after a game is settled — the walk-forward contract."""
        key = (book, sport)
        self.scores[key] = ewma_update(self.scores.get(key), brier(closing_prob, won))
        self.counts[key] = self.counts.get(key, 0.0) + 1.0


def model_b_fair(book_probs: dict[str, float], state: SharpnessState,
                 sport: str, exclude_book: str) -> float | None:
    w = state.weights(sport)
    # Cold start: no resolved games yet -> equal weights over quoting books.
    if not w:
        w = {b: 1.0 for b in book_probs}
    try:
        return loo_consensus(book_probs, w, exclude_book=exclude_book)
    except ValueError:
        return None


def model_h_fair(book_probs: dict[str, float], state: SharpnessState, sport: str,
                 exclude_book: str, anchor_prob: float | None,
                 anchor_is_tight: bool) -> float | None:
    """Plan §4.5 Model H: anchor when Pinnacle quoted both sides within 30 min
    of tipoff (anchor_is_tight), else Model B consensus."""
    if anchor_prob is not None and anchor_is_tight:
        return anchor_prob
    return model_b_fair(book_probs, state, sport, exclude_book)
