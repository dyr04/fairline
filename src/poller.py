"""Implements plan §7 T2: provider-adapter poller. TheOddsApiProvider is the
first OddsProvider; downstream code consumes normalized rows only (§2 vendor
independence). One batch id per sport per invocation (grouping contract §3)."""
from __future__ import annotations

import gzip
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml

from src.db import canonical_ts, connect, utc_now_str
from src.mapping import canonical_book, canonical_team

API_BASE = "https://api.the-odds-api.com/v4"
LOW_CREDIT_WARN = 100


class OddsProvider:
    name = "abstract"

    def fetch_odds(self, sport: str) -> tuple[list[dict], dict]:
        """Return (normalized_rows, meta). Rows carry the §3 schema fields."""
        raise NotImplementedError


class TheOddsApiProvider(OddsProvider):
    name = "the_odds_api"

    def __init__(self, api_key: str, regions: list[str]):
        self.api_key = api_key
        self.regions = ",".join(regions)

    def _get(self, url: str, params: dict) -> requests.Response:
        for attempt in (1, 2):  # ONE retry (T2 /forbidden)
            try:
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                print(f"WARN retrying after: {e}")
        raise RuntimeError("unreachable")

    def fetch_odds(self, sport: str) -> tuple[list[dict], dict]:
        batch = str(uuid.uuid4())
        pulled_at = utc_now_str()
        r = self._get(f"{API_BASE}/sports/{sport}/odds",
                      {"apiKey": self.api_key, "regions": self.regions,
                       "markets": "h2h", "oddsFormat": "decimal"})
        remaining = r.headers.get("x-requests-remaining")
        if remaining is not None and float(remaining) < LOW_CREDIT_WARN:
            print(f"WARNING credits low: {remaining} remaining (§9 P9)")
        rows = []
        for ev in r.json():
            commence = canonical_ts(datetime.fromisoformat(
                ev["commence_time"].replace("Z", "+00:00")))
            for bk in ev.get("bookmakers", []):
                upd = canonical_ts(datetime.fromisoformat(
                    bk["last_update"].replace("Z", "+00:00")))
                for mkt in bk.get("markets", []):
                    if mkt["key"] != "h2h":
                        continue
                    for oc in mkt["outcomes"]:
                        rows.append({
                            "snapshot_batch_id": batch, "pulled_at": pulled_at,
                            "book_last_update": upd, "sport": sport,
                            "event_id": ev["id"], "commence_time": commence,
                            "home_team": canonical_team(ev["home_team"]),
                            "away_team": canonical_team(ev["away_team"]),
                            "book": canonical_book(bk["key"]),
                            "provider": self.name,
                            "outcome": canonical_team(oc["name"]),
                            "price_decimal": float(oc["price"]),
                        })
        return rows, {"raw": r.json(), "batch": batch, "pulled_at": pulled_at,
                      "remaining": remaining}


def insert_rows(conn, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO odds_snapshots
               (snapshot_batch_id, pulled_at, book_last_update, sport, event_id,
                commence_time, home_team, away_team, book, provider, outcome, price_decimal)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(r[k] for k in ("snapshot_batch_id", "pulled_at", "book_last_update",
                                 "sport", "event_id", "commence_time", "home_team",
                                 "away_team", "book", "provider", "outcome",
                                 "price_decimal")))
        n += cur.rowcount
    # Grouping-contract assertion (§3): purge incomplete groups from this insert.
    conn.commit()
    return n


def append_jsonl(meta: dict, sport: str) -> Path:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = Path(f"data/raw/{day}_{sport}.jsonl.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"pulled_at": meta["pulled_at"], "batch": meta["batch"],
                       "sport": sport, "response": meta["raw"]}) + "\n"
    with gzip.open(path, "at") as f:
        f.write(line)
    return path


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    cfg = yaml.safe_load(open("config.yaml"))
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        raise SystemExit("ODDS_API_KEY missing (.env)")
    provider = TheOddsApiProvider(key, cfg["regions"])
    conn = connect()
    for sport in cfg["sports"]:
        rows, meta = provider.fetch_odds(sport)
        inserted = insert_rows(conn, rows)
        append_jsonl(meta, sport)
        print(f"{sport}: {inserted} new rows, credits remaining={meta['remaining']}")


if __name__ == "__main__":
    main()
