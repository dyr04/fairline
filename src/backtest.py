"""Implements plan §4.3: deterministic backtest with the honesty machinery.

Headline output is the EXECUTION-LAG TABLE (edge at 0/1/3/5/10 min of latency):
the naive fill at signal-fire price is a price a manual bettor never touches,
so every other metric is downstream of this table. Also enforced here:
signal-staleness exclusion, closing-line quality tiers, EV-bucket calibration
on a chronological 60/40 split, walk-forward Model B/H weights (§9 P1), and a
look-ahead test hook (tests/test_backtest.py).
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from src.db import parse_ts
from src.devig import devig_multiplicative
from src.sharpness import SharpnessState
from src.signal_engine import compute_signals

LAGS_MIN = [0, 1, 3, 5, 10]
STALENESS_LIMIT_S = 10 * 60
CLV_TIERS = [("tight", 0, 20), ("acceptable", 20, 45), ("stale", 45, 10**9)]


# ---------- data access (pure over pre-fetched rows for determinism) ----------

def rows_by_event_batch(snapshot_rows: list[dict]) -> dict[str, list[tuple[str, list[dict]]]]:
    """event_id -> [(pulled_at, rows_of_that_event_up_to_and_incl_batch)] in time order."""
    by_event: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in snapshot_rows:
        by_event[r["event_id"]][r["snapshot_batch_id"]].append(r)
    out = {}
    for ev, batches in by_event.items():
        ordered = sorted(batches.values(), key=lambda rs: rs[0]["pulled_at"])
        cum: list[dict] = []
        series = []
        for rs in ordered:
            cum = cum + rs
            series.append((rs[0]["pulled_at"], list(cum)))
        out[ev] = series
    return out


def closing_group(event_rows: list[dict], book: str) -> tuple[list[dict] | None, float]:
    """Last complete pre-commence group for a book + minutes_before_commence."""
    commence = parse_ts(event_rows[0]["commence_time"])
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in event_rows:
        if r["book"] == book and parse_ts(r["pulled_at"]) < commence:
            groups[r["snapshot_batch_id"]].append(r)
    complete = [g for g in groups.values() if len(g) == 2]
    if not complete:
        return None, float("inf")
    last = max(complete, key=lambda g: g[0]["pulled_at"])
    mins = (commence - parse_ts(last[0]["pulled_at"])).total_seconds() / 60.0
    return last, mins


def clv_tier(minutes: float) -> str:
    for name, lo, hi in CLV_TIERS:
        if lo <= minutes < hi:
            return name
    return "stale"


# ------------------------------- replay core ---------------------------------

def replay(snapshot_rows: list[dict], results: dict[str, str], config: dict,
           lag_minutes: int = 0, model: str = "A") -> list[dict]:
    """Chronological replay -> settled bets. One bet per (event, book, outcome):
    the first batch where the signal fires, repriced at the first batch >= lag
    minutes later (bet skipped if none before commence — §4.3 lag sweep)."""
    series = rows_by_event_batch(snapshot_rows)
    state = SharpnessState()  # walk-forward: updated only after settlement below
    bets: list[dict] = []
    taken: set[tuple] = set()

    # Global chronological order across events by batch pulled_at.
    timeline: list[tuple[str, str, list[dict]]] = []
    for ev, batches in series.items():
        for pulled_at, cum_rows in batches:
            timeline.append((pulled_at, ev, cum_rows))
    timeline.sort(key=lambda t: t[0])

    settled_events: set[str] = set()
    for pulled_at, ev, cum_rows in timeline:
        commence = parse_ts(cum_rows[0]["commence_time"])
        # Walk-forward settlement: any event whose commence passed and has a
        # result updates sharpness BEFORE later signals are evaluated.
        for done_ev in list(series):
            if done_ev in settled_events or done_ev not in results:
                continue
            done_rows = series[done_ev][-1][1]
            if parse_ts(done_rows[0]["commence_time"]) < parse_ts(pulled_at):
                _settle_sharpness(done_rows, results[done_ev], state)
                settled_events.add(done_ev)

        sigs = compute_signals(cum_rows, config)
        for s in sigs:
            key = (s.event_id, s.book, s.outcome)
            if s.status != "active" or key in taken:
                continue
            fair = _fair_for_model(s, cum_rows, state, model, config)
            if fair is None:
                continue
            edge = s.edge
            if model in ("B", "H"):
                # sig.edge = fair_A - implied_soft  =>  implied_soft = fair_A - edge
                implied_soft = s.fair_prob - s.edge
                edge = fair - implied_soft
                if not (config.get("edge_threshold", 0.02) <= edge
                        <= config.get("max_edge", 0.08)):
                    continue
            # Reprice at lag.
            fill = _fill_at_lag(series[ev], pulled_at, s.book, s.outcome,
                                lag_minutes, commence)
            if fill is None:
                continue
            price, fill_ts, book_upd = fill
            staleness = (parse_ts(fill_ts) - parse_ts(book_upd)).total_seconds()
            taken.add(key)
            bets.append({
                "event_id": ev, "book": s.book, "outcome": s.outcome,
                "sport": s.sport, "entry_price": price, "fair_prob": s.fair_prob,
                "edge": edge, "ts": fill_ts, "stale": staleness > STALENESS_LIMIT_S,
            })
    # Settle bets.
    settled = []
    for b in bets:
        if b["event_id"] not in results:
            continue
        b["won"] = int(results[b["event_id"]] == b["outcome"])
        settled.append(b)
    return settled


def _settle_sharpness(event_rows: list[dict], winner: str, state: SharpnessState) -> None:
    books = {r["book"] for r in event_rows}
    for book in books:
        grp, _mins = closing_group(event_rows, book)
        if grp is None:
            continue
        probs = devig_multiplicative([r["price_decimal"] for r in grp])
        for r, p in zip(grp, probs):
            state.update(book, r["sport"], p, int(r["outcome"] == winner))


def _fair_for_model(sig, cum_rows, state: SharpnessState, model: str, config) -> float | None:
    if model == "A":
        return sig.fair_prob
    # Build per-book devigged probs for this outcome from latest complete groups.
    from src.db import complete_groups
    latest: dict[str, list[dict]] = {}
    for (_, book, _b), rows in complete_groups(cum_rows).items():
        cur = latest.get(book)
        if cur is None or rows[0]["pulled_at"] > cur[0]["pulled_at"]:
            latest[book] = rows
    probs = {}
    for book, rows in latest.items():
        odds = [r["price_decimal"] for r in rows]
        if any(o < 1.05 or o > 30.0 for o in odds):
            continue  # price-sanity guard (mirror _consensus_signals)
        try:
            dv = devig_multiplicative(odds)
        except ValueError:
            continue
        for r, p in zip(rows, dv):
            if r["outcome"] == sig.outcome:
                probs[book] = p
    if model == "B":
        from src.sharpness import model_b_fair
        return model_b_fair(probs, state, sig.sport, exclude_book=sig.book)
    from src.sharpness import model_h_fair
    anchor = config.get("anchor_book", "pinnacle")
    return model_h_fair(probs, state, sig.sport, sig.book,
                        probs.get(anchor), anchor_is_tight=anchor in probs)


def _fill_at_lag(batches, signal_ts, book, outcome, lag_minutes, commence):
    from datetime import timedelta
    target = parse_ts(signal_ts) + timedelta(minutes=lag_minutes)
    for pulled_at, cum_rows in batches:
        t = parse_ts(pulled_at)
        if t >= target and t < commence:
            cands = [r for r in cum_rows
                     if r["book"] == book and r["outcome"] == outcome
                     and r["pulled_at"] == pulled_at]
            if cands:
                r = cands[-1]
                return r["price_decimal"], pulled_at, r["book_last_update"]
    return None


# --------------------------------- metrics -----------------------------------

def metrics(bets: list[dict], snapshot_rows: list[dict], config: dict,
            bankroll: float = 1000.0) -> dict:
    """Flat-unit + quarter-Kelly metrics with staleness exclusion and CLV tiers."""
    from src.staking import kelly_stake
    primary = [b for b in bets if not b["stale"]]
    excluded_frac = 1.0 - (len(primary) / len(bets)) if bets else 0.0
    out = {"n_bets": len(primary), "staleness_excluded_frac": round(excluded_frac, 4)}
    if not primary:
        return out

    for label, stakes in (
        ("flat", [1.0] * len(primary)),
        ("quarter_kelly", [kelly_stake(b["fair_prob"], b["entry_price"], bankroll) or 1.0
                           for b in primary]),
    ):
        rets, equity, peak, mdd = [], 0.0, 0.0, 0.0
        for b, st in zip(primary, stakes):
            pnl = st * (b["entry_price"] - 1.0) if b["won"] else -st
            rets.append(pnl / st)
            equity += pnl
            peak = max(peak, equity)
            mdd = max(mdd, peak - equity)
        roi = sum(r * s for r, s in zip(rets, stakes)) / sum(stakes)
        sharpe = (statistics.mean(rets) / statistics.stdev(rets)
                  if len(rets) > 1 and statistics.stdev(rets) > 0 else 0.0)
        out[label] = {"roi": round(roi, 4), "per_bet_sharpe": round(sharpe, 4),
                      "hit_rate": round(sum(b["won"] for b in primary) / len(primary), 4),
                      "max_drawdown_units": round(mdd, 2)}

    # CLV vs devigged anchor close, tiered (§4.3).
    by_event: dict[str, list[dict]] = defaultdict(list)
    for r in snapshot_rows:
        by_event[r["event_id"]].append(r)
    anchor = config.get("anchor_book", "pinnacle")
    tiers: dict[str, list[float]] = defaultdict(list)
    for b in primary:
        grp, mins = closing_group(by_event[b["event_id"]], anchor)
        if grp is None:
            continue
        probs = devig_multiplicative([r["price_decimal"] for r in grp])
        close_p = {r["outcome"]: p for r, p in zip(grp, probs)}[b["outcome"]]
        clv = close_p - 1.0 / b["entry_price"]  # prob points vs entry implied
        tiers[clv_tier(mins)].append(clv)
    out["clv_by_tier"] = {
        t: {"n": len(v), "avg_clv": round(statistics.mean(v), 4),
            "pct_beating_close": round(sum(1 for x in v if x > 0) / len(v), 4)}
        for t, v in tiers.items() if v}
    headline = tiers.get("tight", []) + tiers.get("acceptable", [])
    out["headline_clv"] = round(statistics.mean(headline), 4) if headline else None
    return out


def lag_table(snapshot_rows: list[dict], results: dict[str, str], config: dict,
              model: str = "A") -> dict:
    """THE first-reported table (§4.3): metrics at each execution lag."""
    return {f"lag_{m}m": metrics(replay(snapshot_rows, results, config, m, model),
                                 snapshot_rows, config)
            for m in LAGS_MIN}


def ev_buckets(bets: list[dict]) -> dict:
    """Promised vs realized by edge bucket; cap set on 60/40 chronological split (§4.3)."""
    edges = [(0.0, 0.02), (0.02, 0.04), (0.04, 0.08), (0.08, 1.0)]
    bets = sorted(bets, key=lambda b: b["ts"])
    cut = int(len(bets) * 0.6)
    out = {}
    for fold, chunk in (("calibration", bets[:cut]), ("validation", bets[cut:])):
        fold_out = {}
        for lo, hi in edges:
            sel = [b for b in chunk if lo <= b["edge"] < hi]
            if not sel:
                continue
            realized = statistics.mean(
                (b["entry_price"] - 1.0) if b["won"] else -1.0 for b in sel)
            fold_out[f"{lo:.0%}-{hi:.0%}"] = {
                "n": len(sel),
                "promised_ev": round(statistics.mean(b["edge"] for b in sel), 4),
                "realized_roi": round(realized, 4)}
        out[fold] = fold_out
    return out


def main() -> None:
    import yaml

    from src.db import connect
    cfg = yaml.safe_load(open("config.yaml"))
    conn = connect()
    conn.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}
    rows = conn.execute("SELECT * FROM odds_snapshots ORDER BY pulled_at").fetchall()
    results = {r["event_id"]: r["winner"]
               for r in conn.execute("SELECT event_id, winner FROM game_results")}
    report = {}
    for model in ("A", "B", "H"):
        report[f"model_{model}"] = lag_table(rows, results, cfg, model)
    base = replay(rows, results, {**cfg, "max_edge": 1.0})  # cap disabled for buckets
    report["ev_buckets_cap_disabled"] = ev_buckets(base)
    json.dump(report, open("results/metrics.json", "w"), indent=2)
    print(json.dumps({"n_snapshot_rows": len(rows), "n_results": len(results)}, indent=2))
    print("wrote results/metrics.json")


if __name__ == "__main__":
    main()
