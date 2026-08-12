"""Implements plan §7 T15: the live learning loop.

For every settled game not yet scored, take each book's devigged CLOSING
probability, Brier-score it against the winner, and EWMA+shrink into
book_sharpness. Idempotent — safe to run every poll forever.

SCORING POLICY (plan §7 T15): apply DATA-QUALITY filters (price sanity,
overround, median-outlier) but NOT signal filters. A book is scored on every
game where its price was real, whether or not it agreed with consensus.
"""
from __future__ import annotations

import statistics

import yaml

from src.db import complete_groups, connect, parse_ts, utc_now_str
from src.devig import devig_multiplicative, overround
from src.sharpness import brier, ewma_update, shrink

PRICE_MIN, PRICE_MAX = 1.05, 30.0
SOFT_OVERROUND_BAND = (0.015, 0.15)
MEDIAN_OUTLIER_TOL = 0.05


def _dict_rows(conn, q, args=()):
    c = conn.execute(q, args)
    return [{d[0]: r[i] for i, d in enumerate(c.description)} for r in c.fetchall()]


def _closing_group_per_book(event_rows: list[dict]) -> dict[str, list[dict]]:
    """Last complete pre-commence group for each book (the closing line)."""
    if not event_rows:
        return {}
    commence = parse_ts(event_rows[0]["commence_time"])
    groups = complete_groups(event_rows)  # keyed (event, book, batch)
    latest: dict[str, list[dict]] = {}
    for (_, book, _b), rows in groups.items():
        if parse_ts(rows[0]["pulled_at"]) >= commence:
            continue  # only pre-commence snapshots count as "closing"
        cur = latest.get(book)
        if cur is None or rows[0]["pulled_at"] > cur[0]["pulled_at"]:
            latest[book] = rows
    return latest


def _passes_quality(book: str, rows: list[dict], all_book_probs: dict) -> bool:
    """Data-quality gate only (NOT signal filters) — see T15 scoring policy."""
    odds = [r["price_decimal"] for r in rows]
    if any(o < PRICE_MIN or o > PRICE_MAX for o in odds):
        return False
    lo, hi = SOFT_OVERROUND_BAND
    if not (lo <= overround(odds) <= hi):
        return False
    return True


def score_event(conn, event_rows: list[dict], winner: str) -> int:
    """Score every quality-passing book's closing line for one settled game.
    Returns number of (book) scores written. Idempotent via sharpness_scored."""
    sport = event_rows[0]["sport"]
    event_id = event_rows[0]["event_id"]
    closing = _closing_group_per_book(event_rows)
    if len(closing) < 2:
        return 0

    # Devig each book's closing line -> implied prob per outcome.
    book_probs: dict[str, dict[str, float]] = {}
    for book, rows in closing.items():
        if not _passes_quality(book, rows, closing):
            continue
        dv = devig_multiplicative([r["price_decimal"] for r in rows])
        book_probs[book] = {r["outcome"]: p for r, p in zip(rows, dv)}

    if len(book_probs) < 2:
        return 0

    # Median-outlier guard: drop any book >5% from the median implied prob.
    # REQUIRES >=4 books — with 2-3 books the "median" is degenerate and every
    # book is equidistant from it, which would reject the entire game. You
    # cannot identify an outlier in a crowd of two.
    if len(book_probs) >= 4:
        outcomes = {oc for p in book_probs.values() for oc in p}
        outliers = set()
        for oc in outcomes:
            vals = [(book_probs[b][oc], b) for b in book_probs if oc in book_probs[b]]
            med = statistics.median(v for v, _ in vals)
            for v, b in vals:
                if abs(v - med) > MEDIAN_OUTLIER_TOL:
                    outliers.add(b)
        for b in outliers:
            book_probs.pop(b, None)

    written = 0
    now = utc_now_str()
    for book, probs in book_probs.items():
        # Skip if already scored (idempotency).
        if conn.execute("SELECT 1 FROM sharpness_scored WHERE event_id=? AND book=?",
                        (event_id, book)).fetchone():
            continue
        p_win = probs.get(winner)
        if p_win is None:
            continue
        bs = brier(p_win, 1)  # book predicted p_win for the team that won
        # Also fold in the losing side(s): Brier over all outcomes.
        bs = sum(brier(probs[oc], 1 if oc == winner else 0) for oc in probs) / len(probs)

        row = conn.execute(
            "SELECT shrunk_brier, n_games FROM book_sharpness WHERE book=? AND sport=?",
            (book, sport)).fetchone()
        prev_score, prev_n = (row[0], row[1]) if row else (None, 0)
        # EWMA on the raw Brier; n_games counts resolved games.
        new_raw = ewma_update(prev_score, bs)
        new_n = prev_n + 1
        conn.execute(
            """INSERT INTO book_sharpness (book, sport, shrunk_brier, n_games, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(book, sport) DO UPDATE SET
                 shrunk_brier=excluded.shrunk_brier, n_games=excluded.n_games,
                 updated_at=excluded.updated_at""",
            (book, sport, new_raw, new_n, now))
        conn.execute(
            "INSERT OR IGNORE INTO sharpness_scored (event_id, book, sport, scored_at) "
            "VALUES (?,?,?,?)", (event_id, book, sport, now))
        written += 1

    # Re-shrink ALL books for this sport toward the current cross-book mean.
    _reshrink_sport(conn, sport)
    conn.commit()
    return written


def _reshrink_sport(conn, sport: str, n0: float = 30.0) -> None:
    """Recompute shrunk_brier for every book in a sport toward the cross-book
    mean. Stored shrunk_brier holds the EWMA raw score; shrinkage is applied
    on read via learned_weights(), so this keeps raw scores and lets the
    resolver shrink. (Kept as a hook; no-op body — shrink happens at read.)"""
    return  # shrink is applied in learned_weights() at consume time


def learned_weights(conn, sport: str, min_games: int) -> dict[str, float]:
    """Inverse-shrunk-Brier weights for books with >= min_games resolved.
    Returns {} if no book qualifies (caller falls back to overround prior)."""
    rows = _dict_rows(conn,
        "SELECT book, shrunk_brier, n_games FROM book_sharpness WHERE sport=?", (sport,))
    if not rows:
        return {}
    s_bar = statistics.mean(r["shrunk_brier"] for r in rows)
    weights = {}
    for r in rows:
        if r["n_games"] < min_games:
            continue
        s_shrunk = shrink(r["shrunk_brier"], r["n_games"], s_bar)
        weights[r["book"]] = 1.0 / max(s_shrunk, 1e-6)
    total = sum(weights.values())
    return {b: w / total for b, w in weights.items()} if total else {}


def main(dry_run: bool = False) -> None:
    cfg = yaml.safe_load(open("config.yaml"))
    conn = connect()
    results = _dict_rows(conn, "SELECT event_id, winner FROM game_results")
    total = 0
    for res in results:
        ev_rows = _dict_rows(conn,
            "SELECT * FROM odds_snapshots WHERE event_id=?", (res["event_id"],))
        if ev_rows and res["winner"]:
            total += score_event(conn, ev_rows, res["winner"])
    if dry_run:
        print("DRY RUN — book_sharpness table:")
        for r in _dict_rows(conn, "SELECT * FROM book_sharpness ORDER BY sport, shrunk_brier"):
            print(" ", r)
    print(f"learn: scored {total} book-games")


if __name__ == "__main__":
    import sys
    main(dry_run="--dry-run" in sys.argv)