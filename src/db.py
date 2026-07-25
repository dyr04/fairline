"""Implements plan §3: schema, canonical timestamps, grouping contract.

CANONICAL TIMESTAMP FORMAT: '%Y-%m-%dT%H:%M:%SZ' — UTC, no microseconds, Z suffix.
SQLite compares timestamps as strings; a single row in another format silently
corrupts ordering, closing-line derivation, and arb-window durations (§9 P-list).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now_str() -> str:
    """The ONLY sanctioned way to produce a timestamp anywhere in this repo."""
    return datetime.now(UTC).strftime(TS_FMT)


def canonical_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are forbidden; attach timezone.utc")
    return dt.astimezone(UTC).strftime(TS_FMT)


def parse_ts(ts: str) -> datetime:
    if not TS_RE.match(ts):
        raise ValueError(f"non-canonical timestamp: {ts!r}")
    return datetime.strptime(ts, TS_FMT).replace(tzinfo=UTC)


SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshots (
  id INTEGER PRIMARY KEY,
  snapshot_batch_id TEXT NOT NULL,
  pulled_at TEXT NOT NULL,
  book_last_update TEXT NOT NULL,
  sport TEXT NOT NULL,
  event_id TEXT NOT NULL,
  commence_time TEXT NOT NULL,
  home_team TEXT, away_team TEXT,
  book TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'the_odds_api',
  outcome TEXT NOT NULL,
  price_decimal REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event ON odds_snapshots(event_id, pulled_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_row
  ON odds_snapshots(event_id, book, outcome, snapshot_batch_id);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  snapshot_batch_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  book TEXT NOT NULL,
  outcome TEXT NOT NULL,
  price_decimal REAL NOT NULL,
  fair_prob REAL NOT NULL,
  edge REAL NOT NULL,
  model TEXT NOT NULL DEFAULT 'A',
  status TEXT NOT NULL,
  rejection_reason TEXT,
  alert_expiry TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_signal
  ON signals(event_id, book, outcome, snapshot_batch_id);

CREATE TABLE IF NOT EXISTS arb_events (
  id INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  books TEXT NOT NULL,          -- json list of (book, outcome, price)
  margin REAL NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_arb ON arb_events(event_id, books);

CREATE TABLE IF NOT EXISTS game_results (
  event_id TEXT PRIMARY KEY,
  sport TEXT,
  winner TEXT,
  home_score INTEGER,
  away_score INTEGER,
  ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS live_bets (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER,            -- NULL for external-source bets
  signal_source TEXT NOT NULL DEFAULT 'internal',
  book TEXT NOT NULL,
  outcome TEXT NOT NULL,
  signal_or_quoted_price REAL NOT NULL,
  achieved_price REAL,
  fair_prob_at_log REAL,
  staked REAL NOT NULL DEFAULT 0,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_health (
  id INTEGER PRIMARY KEY,
  book TEXT NOT NULL,
  date TEXT NOT NULL,
  max_bet_observed REAL,
  restriction_flag INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS book_sharpness (
  book TEXT NOT NULL,
  sport TEXT NOT NULL,
  shrunk_brier REAL NOT NULL,
  n_games INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (book, sport)
);

CREATE TABLE IF NOT EXISTS alert_log (
  key TEXT PRIMARY KEY,         -- event|book|outcome
  last_alerted TEXT NOT NULL
);
"""


def connect(path: str = "data/odds.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def complete_groups(rows: list[dict], n_outcomes: int = 2) -> dict[tuple, list[dict]]:
    """GROUPING CONTRACT (§3): group by (event_id, book, snapshot_batch_id) and
    return only complete groups. Grouping by pulled_at can mix vintages; never do it."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["event_id"], r["book"], r["snapshot_batch_id"]), []).append(r)
    return {k: v for k, v in groups.items() if len(v) == n_outcomes}
