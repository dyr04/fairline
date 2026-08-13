"""One-off: is replay collapsing time instead of stepping through batches?"""
import yaml

from src.db import connect
from src.signal_engine import compute_signals

c = connect()
c.row_factory = lambda cu, r: {d[0]: r[i] for i, d in enumerate(cu.description)}
cfg = yaml.safe_load(open("config.yaml"))

ev = "58beff9061f15ff3f416542cb51f4751"
rows = c.execute("SELECT * FROM odds_snapshots WHERE event_id=?", (ev,)).fetchall()
batches = sorted(set(r["snapshot_batch_id"] for r in rows))
print("distinct batches for this event:", len(batches))

early = [r for r in rows if r["snapshot_batch_id"] == batches[0]]
print("earliest batch rows:", len(early))

sigs_early = compute_signals(early, cfg)
active_early = [s for s in sigs_early if s.status == "active"]
print("earliest batch alone -> signals:", len(sigs_early), "active:", len(active_early))

sigs_all = compute_signals(rows, cfg)
active_all = [s for s in sigs_all if s.status == "active"]
print("all rows together    -> signals:", len(sigs_all), "active:", len(active_all))

# Show rejection reasons on the full-history call
from collections import Counter
reasons = Counter(s.rejection_reason for s in sigs_all)
print("rejection reasons (all rows):", dict(reasons))

print("\n--- why are books dropped before signals? ---")
from src.db import complete_groups
from src.devig import overround
groups = complete_groups(early)
print("complete 2-outcome groups (book-batches):", len(groups))
latest = {}
for (_, book, _b), grp in groups.items():
    cur = latest.get(book)
    if cur is None or grp[0]["pulled_at"] > cur[0]["pulled_at"]:
        latest[book] = grp
print("distinct books with complete groups:", len(latest))
for book, grp in latest.items():
    odds = [r["price_decimal"] for r in grp]
    ovr = overround(odds)
    print(f"  {book:16} overround={ovr:+.3f}  prices={[round(o,2) for o in odds]}")

print("\n--- what path is compute_signals taking? ---")
print("fair_line_mode in cfg:", cfg.get("fair_line_mode"))
from src import signal_engine
# monkey-check: call _consensus_signals directly on the 14-book latest set
out = []
result = signal_engine._consensus_signals(latest, ev, "basketball_wnba", cfg, out)
active = [s for s in result if s.status == "active"]
rej = {}
for s in result:
    rej[s.rejection_reason] = rej.get(s.rejection_reason, 0) + 1
print("_consensus_signals direct -> total:", len(result), "active:", len(active))
print("  reasons:", rej)