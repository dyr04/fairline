"""Implements plan §7 T6: results from The Odds API /scores ONLY (no scraping)."""
from __future__ import annotations

import os

import requests
import yaml

from src.db import connect, utc_now_str
from src.mapping import canonical_team

API_BASE = "https://api.the-odds-api.com/v4"


def fetch_scores(api_key: str, sport: str) -> list[dict]:
    r = requests.get(f"{API_BASE}/sports/{sport}/scores",
                     params={"apiKey": api_key, "daysFrom": 2}, timeout=20)
    r.raise_for_status()
    return r.json()


def ingest(conn, sport: str, scores: list[dict]) -> int:
    n = 0
    for ev in scores:
        if not ev.get("completed"):
            continue
        s = {canonical_team(x["name"]): int(x["score"]) for x in (ev.get("scores") or [])}
        home, away = canonical_team(ev["home_team"]), canonical_team(ev["away_team"])
        if home not in s or away not in s:
            print(f"skip {ev['id']}: missing/void scores")
            continue
        winner = home if s[home] > s[away] else away
        cur = conn.execute(
            """INSERT INTO game_results
                (event_id, sport, winner, home_score, away_score, ingested_at)
                VALUES (?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET winner=excluded.winner,
                 home_score=excluded.home_score, away_score=excluded.away_score""",
            (ev["id"], sport, winner, s[home], s[away], utc_now_str()))
        n += cur.rowcount
    conn.commit()
    return n


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    cfg = yaml.safe_load(open("config.yaml"))
    key = os.environ["ODDS_API_KEY"]
    conn = connect()
    for sport in cfg["sports"]:
        n = ingest(conn, sport, fetch_scores(key, sport))
        print(f"{sport}: {n} results upserted")
        orphans = conn.execute(
            """SELECT COUNT(*) FROM game_results g
               WHERE NOT EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.event_id=g.event_id)"""
        ).fetchone()[0]
        print(f"orphan results: {orphans} (must be 0)")


if __name__ == "__main__":
    main()
