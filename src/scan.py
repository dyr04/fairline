"""The scan job (T5 step 3g): signals + arbs from the latest data, then alerts."""
from __future__ import annotations

import json

import yaml

from src.alerts import send
from src.db import connect, utc_now_str
from src.signal_engine import compute_signals, scan_arbs, write_signals


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    cfg = yaml.safe_load(open("config.yaml"))
    conn = connect()
    conn.row_factory = None
    cur = conn.execute("SELECT DISTINCT event_id FROM odds_snapshots")
    def dict_rows(q, a=()):
        c = conn.execute(q, a)
        return [{d[0]: r[i] for i, d in enumerate(c.description)} for r in c.fetchall()]
    n_sig = n_arb = 0
    for (event_id,) in cur.fetchall():
        rows = dict_rows("SELECT * FROM odds_snapshots WHERE event_id=?", (event_id,))
        sigs = compute_signals(rows, cfg)
        n_sig += write_signals(conn, sigs)
        for s in sigs:
            send(s, cfg, conn)
        for arb in scan_arbs(rows, cfg):
            now = utc_now_str()
            conn.execute(
                """INSERT INTO arb_events (event_id, sport, books, margin, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(event_id, books) DO UPDATE SET
                     last_seen=excluded.last_seen, margin=excluded.margin""",
                (arb["event_id"], arb["sport"], arb["books"], arb["margin"], now, now))
            n_arb += 1
    conn.commit()
    print(json.dumps({"new_signals": n_sig, "arb_rows": n_arb}))


if __name__ == "__main__":
    main()
