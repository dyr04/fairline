"""Plan §7 T7 step 2: rebuild sqlite from data-branch JSONL via manifest.json
ONLY (never directory-guess). Falls back to a local sqlite for development.

The deployed dashboard (Streamlit Cloud) can't see the local SQLite, so it
rebuilds an in-memory DB from the public data-branch JSONL each load. Raw
responses are replayed through the SAME poller normalization used live, so the
dashboard's data is identical to what the pipeline produced (no parallel logic).
"""
import gzip
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

# Public data branch — the deployed app reads from here.
REPO_RAW = "https://raw.githubusercontent.com/dyr04/fairline/data"


@st.cache_data(ttl=600)
def _fetch_manifest() -> list[str]:
    r = requests.get(f"{REPO_RAW}/manifest.json", timeout=15)
    r.raise_for_status()
    return r.json()["files"]


@st.cache_data(ttl=600)
def _fetch_file(fname: str) -> bytes:
    r = requests.get(f"{REPO_RAW}/{fname}", timeout=30)
    r.raise_for_status()
    return r.content


def _canonical_ts(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
        timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_db(force_remote: bool = False) -> sqlite3.Connection:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.db import connect
    from src.poller import insert_rows
    from src.mapping import canonical_book, canonical_team

    local = Path(__file__).parent.parent / "data/odds.sqlite"
    if not force_remote and local.exists():
        return sqlite3.connect(local, check_same_thread=False)

    conn = connect(":memory:")
    files = _fetch_manifest()
    total = 0
    for fname in files:
        if not fname.endswith(".jsonl.gz"):
            continue
        try:
            raw = _fetch_file(fname)
            text = gzip.decompress(raw).decode()
        except Exception as e:
            print(f"skip {fname}: {e}")
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rows = _replay_the_odds_api(rec, canonical_book, canonical_team)
            total += insert_rows(conn, rows)
    print(f"data_loader: rebuilt {total} rows from {len(files)} files")
    return conn


def _replay_the_odds_api(rec: dict, canonical_book, canonical_team) -> list[dict]:
    """Turn one stored raw The Odds API response into normalized rows —
    mirrors TheOddsApiProvider.fetch_odds, but from stored JSON not the API."""
    rows = []
    batch = rec.get("batch", "")
    pulled_at = rec.get("pulled_at", "")
    sport = rec.get("sport", "")
    # sport field in the JSONL is like 'the_odds_api_baseball_mlb'; strip provider prefix
    for prefix in ("the_odds_api_", "sportsgameodds_"):
        if sport.startswith(prefix):
            sport = sport[len(prefix):]
    response = rec.get("response", [])
    if isinstance(response, list):
        events = response
    else:
        events = response.get("data", []) if isinstance(response, dict) else []
    for ev in events:
        if "bookmakers" not in ev:
            continue
        try:
            commence = _canonical_ts(ev["commence_time"])
        except Exception:
            continue
        for bk in ev.get("bookmakers", []):
            try:
                upd = _canonical_ts(bk["last_update"])
            except Exception:
                continue
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
                        "book": canonical_book(bk["key"]), "provider": "the_odds_api",
                        "outcome": canonical_team(oc["name"]),
                        "price_decimal": float(oc["price"]),
                    })
    return rows