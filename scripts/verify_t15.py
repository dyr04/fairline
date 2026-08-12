"""Verify T15: Brier scoring, idempotency, cold-start vs learned handoff."""
import sqlite3
import sys

from src.db import connect, canonical_ts
from src.learn import learned_weights, score_event
from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def mk_rows(book, pa, pb, winner_side, event, commence_min=120):
    ts = canonical_ts(T0)
    comm = canonical_ts(T0 + timedelta(minutes=commence_min))
    close = canonical_ts(T0 + timedelta(minutes=commence_min - 10))  # pre-commence
    return [
        {"snapshot_batch_id": f"{event}-{book}", "pulled_at": close,
         "book_last_update": close, "sport": "test", "event_id": event,
         "commence_time": comm, "home_team": "A", "away_team": "B",
         "book": book, "provider": "t", "outcome": "A", "price_decimal": pa},
        {"snapshot_batch_id": f"{event}-{book}", "pulled_at": close,
         "book_last_update": close, "sport": "test", "event_id": event,
         "commence_time": comm, "home_team": "A", "away_team": "B",
         "book": book, "provider": "t", "outcome": "B", "price_decimal": pb},
    ]


conn = connect(":memory:")
# Two books quote 5 games; sharpbook always prices the winner tighter.
for i in range(5):
    ev = f"game{i}"
    rows = mk_rows("sharpbook", 1.5, 2.6, "A", ev) + mk_rows("softbook", 1.9, 1.9, "A", ev)
    for r in rows:
        conn.execute("""INSERT INTO odds_snapshots (snapshot_batch_id,pulled_at,
            book_last_update,sport,event_id,commence_time,home_team,away_team,book,
            provider,outcome,price_decimal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(r[k] for k in ("snapshot_batch_id","pulled_at","book_last_update",
            "sport","event_id","commence_time","home_team","away_team","book",
            "provider","outcome","price_decimal")))
    conn.execute("INSERT INTO game_results (event_id, winner) VALUES (?,?)", (ev, "A"))
conn.commit()

for i in range(5):
    ev_rows = [dict(zip([d[0] for d in conn.execute("SELECT * FROM odds_snapshots LIMIT 0").description],
                        r)) for r in conn.execute("SELECT * FROM odds_snapshots WHERE event_id=?", (f"game{i}",))]
    score_event(conn, ev_rows, "A")

# Idempotency: re-run, scores must not change.
before = list(conn.execute("SELECT book, shrunk_brier, n_games FROM book_sharpness ORDER BY book"))
for i in range(5):
    ev_rows = [dict(zip([d[0] for d in conn.execute("SELECT * FROM odds_snapshots LIMIT 0").description],
                        r)) for r in conn.execute("SELECT * FROM odds_snapshots WHERE event_id=?", (f"game{i}",))]
    score_event(conn, ev_rows, "A")
after = list(conn.execute("SELECT book, shrunk_brier, n_games FROM book_sharpness ORDER BY book"))
if before != after:
    sys.exit(f"FAIL idempotency: {before} != {after}")

# sharpbook should have LOWER Brier (better) than softbook.
d = {r[0]: r[1] for r in after}
if d["sharpbook"] >= d["softbook"]:
    sys.exit(f"FAIL: sharpbook Brier {d['sharpbook']:.3f} not < softbook {d['softbook']:.3f}")

# Cold-start: with only 5 games, min_games=30 -> no learned weights yet.
if learned_weights(conn, "test", 30):
    sys.exit("FAIL: learned weights returned below game threshold")
# Learned: min_games=3 -> both books qualify, sharpbook weighted higher.
lw = learned_weights(conn, "test", 3)
if not lw or lw["sharpbook"] <= lw["softbook"]:
    sys.exit(f"FAIL: learned weights wrong: {lw}")

print(f"verify_t15: OK — sharpbook Brier {d['sharpbook']:.3f} < softbook {d['softbook']:.3f}, "
      f"idempotent, cold-start + learned handoff correct")