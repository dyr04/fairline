"""T2 verify (LIVE — needs ODDS_API_KEY and two poller runs first)."""
import sys

from src.db import TS_RE, connect

conn = connect()
n = conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0]
if n == 0:
    sys.exit("FAIL: no rows — run python -m src.poller twice first")
pin = conn.execute("SELECT COUNT(*) FROM odds_snapshots WHERE book=\"pinnacle\"").fetchone()[0]
assert pin > 0, "FAIL: no pinnacle rows (check eu region)"
for col in ("pulled_at", "book_last_update", "commence_time"):
    for (v,) in conn.execute(f"SELECT DISTINCT {col} FROM odds_snapshots LIMIT 500"):
        assert TS_RE.match(v), f"FAIL non-canonical {col}: {v}"
incomplete = conn.execute("""SELECT COUNT(*) FROM (SELECT event_id, book, snapshot_batch_id,
    COUNT(*) c FROM odds_snapshots GROUP BY 1,2,3 HAVING c != 2)""").fetchone()[0]
assert incomplete == 0, f"FAIL: {incomplete} incomplete groups (grouping contract)"
print(f"verify_t2: OK ({n} rows, {pin} pinnacle)")
