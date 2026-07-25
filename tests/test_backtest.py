"""Plan §7 T8: determinism + the look-ahead test (§4.3 bias controls)."""
import copy

from src.backtest import ev_buckets, lag_table, metrics, replay
from tests.synth import five_book_event

CFG = {"anchor_book": "pinnacle", "anchor_overround_band": [0.015, 0.08],
       "soft_overround_band": [0.015, 0.15], "min_books": 4,
       "staleness_sync_minutes": 5, "edge_threshold": 0.02, "max_edge": 0.08,
       "exchange_books": {}, "bankroll": 1000}


def season(n_events=4):
    rows, results = [], {}
    for i in range(n_events):
        ev = f"ev{i}"
        base = i * 300  # events spaced 5h apart
        for b, mins in (("s0", 0), ("s1", 30), ("s2", 90)):  # 3 batches each
            for r in five_book_event():
                r = dict(r)
                r["event_id"] = ev
                r["snapshot_batch_id"] = f"{ev}-{b}-{r['book']}"
                from datetime import timedelta

                from src.db import canonical_ts, parse_ts
                r["pulled_at"] = canonical_ts(
                    parse_ts(r["pulled_at"]) + timedelta(minutes=base + mins))
                r["book_last_update"] = r["pulled_at"]
                r["commence_time"] = canonical_ts(
                    parse_ts(r["commence_time"]) + timedelta(minutes=base))
                rows.append(r)
        results[ev] = "A" if i % 2 == 0 else "B"
    return rows, results


def test_replay_finds_bets_and_is_deterministic():
    rows, results = season()
    b1 = replay(rows, results, CFG)
    b2 = replay(copy.deepcopy(rows), dict(results), CFG)
    assert len(b1) > 0
    assert b1 == b2


def test_look_ahead_corruption_does_not_change_early_bets():
    rows, results = season()
    bets = replay(rows, results, CFG)
    cutoff = sorted(b["ts"] for b in bets)[0]
    corrupted = []
    for r in rows:
        r = dict(r)
        if r["pulled_at"] > cutoff:
            r["price_decimal"] = 9.99  # nuke the future
        corrupted.append(r)
    bets_c = replay(corrupted, results, CFG)
    early = [b for b in bets if b["ts"] <= cutoff]
    early_c = [b for b in bets_c if b["ts"] <= cutoff]
    assert early == early_c


def test_metrics_and_buckets_shape():
    rows, results = season(6)
    bets = replay(rows, results, CFG)
    m = metrics(bets, rows, CFG)
    assert "flat" in m and "quarter_kelly" in m and "clv_by_tier" in m
    lt = lag_table(rows, results, CFG)
    assert set(lt) == {"lag_0m", "lag_1m", "lag_3m", "lag_5m", "lag_10m"}
    assert lt["lag_10m"]["n_bets"] <= lt["lag_0m"]["n_bets"]  # lag can only lose fills
    eb = ev_buckets(bets)
    assert "calibration" in eb and "validation" in eb


def test_models_b_and_h_run():
    rows, results = season(6)
    for model in ("B", "H"):
        assert isinstance(replay(rows, results, CFG, model=model), list)
