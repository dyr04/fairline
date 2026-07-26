"""Implements plan §4.1: the signal engine — the ONLY producer of +EV signals.

Built as pure functions so the T8 backtest replays the EXACT same logic on
historical batches (never a parallel reimplementation — §9 anti-drift).

Filter order (each rejection is logged with its reason; rejection stats are a
writeup exhibit): complete group -> price sanity -> anchor rule -> overround
bands -> min_books -> staleness sync -> consensus outlier -> edge threshold ->
max-edge cap.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass

from src.db import complete_groups, parse_ts, utc_now_str
from src.devig import devig_multiplicative, overround

# Prices outside this band are data artifacts (unmatched exchange orders,
# feed errors), not quotes. Learned the hard way: a 1.01 exchange price with a
# 66% two-sided overround was silently handed the HEAVIEST consensus weight.
PRICE_MIN, PRICE_MAX = 1.05, 30.0
EXCHANGE_OVERROUND_BAND = (0.0, 0.05)


@dataclass
class SignalRow:
    snapshot_batch_id: str
    event_id: str
    sport: str
    book: str
    outcome: str
    price_decimal: float
    fair_prob: float
    edge: float
    model: str
    status: str            # 'active' | 'rejected'
    rejection_reason: str | None


def _exchange_midpoint_prob(rows: list[dict], commission: float) -> dict[str, float]:
    """Plan §4.5: exchanges have no vig; implied prob is the (commission-adjusted)
    midpoint. With only back prices in the feed, adjust price for commission."""
    out = {}
    for r in rows:
        eff_price = 1.0 + (r["price_decimal"] - 1.0) * (1.0 - commission)
        out[r["outcome"]] = 1.0 / eff_price
    total = sum(out.values())
    return {k: v / total for k, v in out.items()}


def compute_signals(snapshot_rows: list[dict], config: dict) -> list[SignalRow]:
    """Evaluate one event's latest snapshot rows -> active + rejected signals.

    snapshot_rows: all rows for ONE event (possibly several books/batches).
    """
    anchor = config.get("anchor_book", "pinnacle")
    a_lo, a_hi = config.get("anchor_overround_band", [0.015, 0.08])
    s_lo, s_hi = config.get("soft_overround_band", [0.015, 0.15])
    exchange_books: dict = config.get("exchange_books", {}) or {}
    out: list[SignalRow] = []
    if not snapshot_rows:
        return out

    sport = snapshot_rows[0]["sport"]
    event_id = snapshot_rows[0]["event_id"]

    # Latest complete group per book (grouping contract, §3).
    groups = complete_groups(snapshot_rows)
    latest: dict[str, list[dict]] = {}
    for (_, book, _batch), rows in groups.items():
        cur = latest.get(book)
        if cur is None or rows[0]["pulled_at"] > cur[0]["pulled_at"]:
            latest[book] = rows

    def reject_all(book: str, rows: list[dict], reason: str) -> None:
        for r in rows:
            out.append(SignalRow(r["snapshot_batch_id"], event_id, sport, book,
                                 r["outcome"], r["price_decimal"], 0.0, 0.0,
                                 "A", "rejected", reason))

    if config.get("fair_line_mode", "anchor") == "consensus":
        return _consensus_signals(latest, event_id, sport, config, out)

    # Anchor rule (§9 P8): no anchor group -> no Model A signals for this event.
    if anchor not in latest:
        for book, rows in latest.items():
            reject_all(book, rows, "no_anchor")
        return out

    anchor_rows = latest[anchor]
    anchor_odds = [r["price_decimal"] for r in anchor_rows]
    if not (a_lo <= overround(anchor_odds) <= a_hi):
        for book, rows in latest.items():
            reject_all(book, rows, "overround")
        return out
    probs = devig_multiplicative(anchor_odds)
    fair = {r["outcome"]: p for r, p in zip(anchor_rows, probs)}

    # Market depth (§4.1): require min_books books quoting this event.
    if len(latest) < int(config.get("min_books", 4)):
        for book, rows in latest.items():
            reject_all(book, rows, "min_books")
        return out

    sync_limit = float(config.get("staleness_sync_minutes", 5)) * 60.0
    anchor_upd = parse_ts(anchor_rows[0]["book_last_update"])

    for book, rows in latest.items():
        if book == anchor:
            continue
        # Staleness sync (§9 P2): only compare books updated near the anchor.
        delta = abs((parse_ts(rows[0]["book_last_update"]) - anchor_upd).total_seconds())
        if delta > sync_limit:
            reject_all(book, rows, "stale_sync")
            continue
        odds = [r["price_decimal"] for r in rows]
        if any(o < PRICE_MIN or o > PRICE_MAX for o in odds):
            reject_all(book, rows, "price_sanity")
            continue
        if book in exchange_books:
            e_lo, e_hi = EXCHANGE_OVERROUND_BAND
            if not (e_lo <= overround(odds) <= e_hi):
                reject_all(book, rows, "overround")
                continue
            implied = _exchange_midpoint_prob(rows, float(exchange_books[book]))
        else:
            if not (s_lo <= overround(odds) <= s_hi):
                reject_all(book, rows, "overround")
                continue
            probs_b = devig_multiplicative(odds)
            implied = {r["outcome"]: p for r, p in zip(rows, probs_b)}
        for r in rows:
            edge = fair[r["outcome"]] - implied[r["outcome"]]
            if edge < float(config.get("edge_threshold", 0.02)):
                status, reason = "rejected", "below_threshold"
            elif edge > float(config.get("max_edge", 0.08)):
                status, reason = "rejected", "max_edge"
            else:
                status, reason = "active", None
            out.append(SignalRow(r["snapshot_batch_id"], event_id, sport, book,
                                 r["outcome"], r["price_decimal"],
                                 fair[r["outcome"]], edge, "A", status, reason))
    return out


def _consensus_signals(latest, event_id, sport, config, out):
    """Fair line = weighted leave-one-out consensus of all quoting books (§4.5).

    Used when no sharp anchor quotes the market. Cold-start weights use inverse
    overround as a sharpness PRIOR (low-vig operators are systematically
    sharper); Brier-learned weights replace it once games resolve.

    Guards, all added after a live run exposed them:
      - price sanity: 1.01-style artifacts never enter the consensus
      - exchange overround MEASURED, never assumed (a 66%-overround "exchange"
        was previously weighted heaviest of all books)
      - consensus outlier: if a majority of books show edge on the same side,
        the CONSENSUS is wrong, not the books
    """
    exchange_books = config.get("exchange_books", {}) or {}
    s_lo, s_hi = config.get("soft_overround_band", [0.015, 0.15])
    sync_limit = float(config.get("staleness_sync_minutes", 5)) * 60.0
    edge_min = float(config.get("edge_threshold", 0.02))
    edge_max = float(config.get("max_edge", 0.08))

    def reject_all(book, rows, reason):
        for r in rows:
            out.append(SignalRow(r["snapshot_batch_id"], event_id, sport, book,
                                 r["outcome"], r["price_decimal"], 0.0, 0.0,
                                 "B", "rejected", reason))

    probs, wts = {}, {}
    for book, rows in latest.items():
        odds = [r["price_decimal"] for r in rows]
        if any(o < PRICE_MIN or o > PRICE_MAX for o in odds):
            reject_all(book, rows, "price_sanity")
            continue
        ovr = overround(odds)
        if book in exchange_books:
            e_lo, e_hi = EXCHANGE_OVERROUND_BAND
            if not (e_lo <= ovr <= e_hi):
                reject_all(book, rows, "overround")
                continue
            p = _exchange_midpoint_prob(rows, float(exchange_books[book]))
        else:
            if not (s_lo <= ovr <= s_hi):
                reject_all(book, rows, "overround")
                continue
            dv = devig_multiplicative(odds)
            p = {r["outcome"]: q for r, q in zip(rows, dv)}
        probs[book] = p
        wts[book] = 1.0 / max(ovr, 0.02)

    if len(probs) < int(config.get("min_books", 4)):
        for book in list(probs):
            reject_all(book, latest[book], "min_books")
        return out

    freshest = max(parse_ts(latest[b][0]["book_last_update"]) for b in probs)
    for book in list(probs):
        rows = latest[book]
        if (freshest - parse_ts(rows[0]["book_last_update"])).total_seconds() > sync_limit:
            reject_all(book, rows, "stale_sync")
            del probs[book]
            del wts[book]

    if len(probs) < int(config.get("min_books", 4)):
        return out

    # Median-outlier guard (same principle as scan_arbs): a book whose implied
    # prob for either outcome is >5% away from the median across all synced
    # books is either stale, or in a market of one. Either way it shouldn't
    # shape the consensus fair line — it distorts the weighted average and
    # manufactures phantom edges against every other book.
    import statistics
    outcomes = {oc for p in probs.values() for oc in p}
    outlier = set()
    for oc in outcomes:
        vals = [(probs[b][oc], b) for b in probs]
        med = statistics.median(v for v, _ in vals)
        for v, b in vals:
            if abs(v - med) > 0.05:
                outlier.add(b)
    for b in outlier:
        reject_all(b, latest[b], "consensus_outlier")
        del probs[b]
        del wts[b]
    if len(probs) < int(config.get("min_books", 4)):
        return out

    def loo_fair(book: str, outcome: str) -> float:
        others = [b for b in probs if b != book]
        tw = sum(wts[b] for b in others)
        return sum(wts[b] * probs[b][outcome] for b in others) / tw

    # Consensus-outlier guard: count books showing edge on each side. If a
    # majority agree the consensus is low on the same outcome, the consensus is
    # the outlier (usually one bad quote dragging the weighted mean).
    side_counts: Counter = Counter()
    for book, p in probs.items():
        for oc in p:
            if loo_fair(book, oc) - p[oc] >= edge_min:
                side_counts[oc] += 1
    consensus_bad = {oc for oc, c in side_counts.items() if c > len(probs) / 2}

    for book, p in probs.items():
        for r in latest[book]:
            oc = r["outcome"]
            if oc in consensus_bad:
                out.append(SignalRow(r["snapshot_batch_id"], event_id, sport, book,
                                     oc, r["price_decimal"], 0.0, 0.0, "B",
                                     "rejected", "consensus_outlier"))
                continue
            fair = loo_fair(book, oc)
            edge = fair - p[oc]
            if edge < edge_min:
                status, reason = "rejected", "below_threshold"
            elif edge > edge_max:
                status, reason = "rejected", "max_edge"
            else:
                status, reason = "active", None
            out.append(SignalRow(r["snapshot_batch_id"], event_id, sport, book,
                                 oc, r["price_decimal"], fair, edge, "B", status, reason))
    return out


def write_signals(conn, signals: list[SignalRow]) -> int:
    """Thin DB wrapper — INSERT OR IGNORE against ux_signal (§3)."""
    n = 0
    for s in signals:
        d = asdict(s)
        cur = conn.execute(
            """INSERT OR IGNORE INTO signals
               (created_at, snapshot_batch_id, event_id, sport, book, outcome,
                price_decimal, fair_prob, edge, model, status, rejection_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (utc_now_str(), d["snapshot_batch_id"], d["event_id"], d["sport"],
             d["book"], d["outcome"], d["price_decimal"], d["fair_prob"],
             d["edge"], d["model"], d["status"], d["rejection_reason"]))
        n += cur.rowcount
    conn.commit()
    return n


def scan_arbs(snapshot_rows: list[dict], config: dict) -> list[dict]:
    """Implements §4.2: n-outcome cross-book arbitrage on synced, complete groups."""
    sync_limit = float(config.get("staleness_sync_minutes", 5)) * 60.0
    groups = complete_groups(snapshot_rows)
    latest: dict[str, list[dict]] = {}
    for (_, book, _b), rows in groups.items():
        cur = latest.get(book)
        if cur is None or rows[0]["pulled_at"] > cur[0]["pulled_at"]:
            latest[book] = rows
    # Price sanity applies here too: a 1.01 artifact would fake an arb leg.
    latest = {b: rows for b, rows in latest.items()
              if all(PRICE_MIN <= r["price_decimal"] <= PRICE_MAX for r in rows)}
    if len(latest) < 2:
        return []
    freshest = max(parse_ts(r[0]["book_last_update"]) for r in latest.values())
    synced = {b: rows for b, rows in latest.items()
              if (freshest - parse_ts(rows[0]["book_last_update"])).total_seconds() <= sync_limit}
    if len(synced) < 2:
        return []

    # Median-outlier guard: reject any book whose implied prob for either outcome
    # is > 5% away from the median across all synced books. This catches the
    # 'Pinnacle stale but timestamp fresh' failure that manufactured phantom arbs
    # on 2026-07-26.
    import statistics
    by_outcome: dict[str, list[tuple[float, str]]] = {}
    for book, rows in synced.items():
        for r in rows:
            by_outcome.setdefault(r["outcome"], []).append((r["price_decimal"], book))
    outlier_books = set()
    for outcome, quotes in by_outcome.items():
        if len(quotes) < 4:
            continue
        implieds = [1.0 / p for p, _ in quotes]
        med = statistics.median(implieds)
        for (p, book) in quotes:
            if abs(1.0 / p - med) > 0.05:
                outlier_books.add(book)
    synced = {b: rows for b, rows in synced.items() if b not in outlier_books}
    if len(synced) < 2:
        return []
    best: dict[str, tuple[float, str]] = {}
    for book, rows in synced.items():
        for r in rows:
            if r["outcome"] not in best or r["price_decimal"] > best[r["outcome"]][0]:
                best[r["outcome"]] = (r["price_decimal"], book)


    margin = 1.0 - sum(1.0 / p for p, _ in best.values())
    if margin <= -0.01:  # log arbs and near-arbs (margin > -1%)
        return []
    ev = snapshot_rows[0]
    return [{
        "event_id": ev["event_id"], "sport": ev["sport"], "margin": margin,
        "books": json.dumps(sorted((b, o, p) for o, (p, b) in best.items())),
    }]


def stake_split(total: float, odds: list[float]) -> list[float]:
    """Equal-payoff arb split: stake_i = S*(1/o_i)/sum(1/o_j) (§4.2)."""
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [total * x / s for x in inv]