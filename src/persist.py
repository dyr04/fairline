"""Persist small DERIVED tables across cron runs as JSONL on the data branch,
sidestepping P4 (no SQLite binary in git) while giving the learning loop memory.

dump_derived() writes game_results + book_sharpness + sharpness_scored to
data/derived/*.jsonl. load_derived() reads them back into a fresh DB at the
start of a run. Raw odds stay in the existing raw/*.jsonl.gz; this is ONLY the
computed state that must survive."""
from __future__ import annotations

import json
from pathlib import Path

from src.db import connect

DERIVED_DIR = Path("data/derived")
TABLES = {
    "game_results": ["event_id", "sport", "winner", "home_score", "away_score", "ingested_at"],
    "book_sharpness": ["book", "sport", "shrunk_brier", "n_games", "updated_at"],
    "sharpness_scored": ["event_id", "book", "sport", "scored_at"],
}


def dump_derived(conn=None) -> None:
    conn = conn or connect()
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    for table, cols in TABLES.items():
        # table may not have all cols (game_results schema varies); guard.
        try:
            rows = conn.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
        except Exception:
            continue
        path = DERIVED_DIR / f"{table}.jsonl"
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(dict(zip(cols, r))) + "\n")
    print(f"persist: dumped {len(TABLES)} derived tables")


def load_derived(conn=None) -> None:
    conn = conn or connect()
    for table, cols in TABLES.items():
        path = DERIVED_DIR / f"{table}.jsonl"
        if not path.exists():
            continue
        placeholders = ",".join("?" * len(cols))
        n = 0
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                tuple(rec.get(c) for c in cols))
            n += 1
        print(f"persist: loaded {n} rows into {table}")
    conn.commit()


if __name__ == "__main__":
    import sys
    if "--dump" in sys.argv:
        dump_derived()
    elif "--load" in sys.argv:
        load_derived()