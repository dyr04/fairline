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


class SportsGameOddsProvider(OddsProvider):
    """Second odds source (plan §7 T13). Merges into odds_snapshots alongside
    The Odds API via canonical mapping; pregame events only; American -> decimal."""
    name = "sportsgameodds"
    BASE = "https://api.sportsgameodds.com/v2/events"
    LEAGUE_TO_SPORT = {"MLB": "baseball_mlb", "NBA": "basketball_nba",
                       "NFL": "americanfootball_nfl", "NHL": "icehockey_nhl"}

    def __init__(self, api_key: str, leagues: list[str]):
        self.api_key = api_key
        self.leagues = leagues

    def fetch_odds(self, sport: str) -> tuple[list[dict], dict]:
        from src.mapping import american_to_decimal, canonical_book, canonical_team
        league = next((lg for lg, sp in self.LEAGUE_TO_SPORT.items() if sp == sport), None)
        if league not in self.leagues:
            return [], {"raw": [], "batch": "", "pulled_at": "", "remaining": "n/a"}

        batch = str(uuid.uuid4())
        pulled_at = utc_now_str()
        rows, raw, cursor, calls, events_seen = [], [], None, 0, 0
        deeplinks: list[tuple] = []
        for _ in range(3):  # paginate up to 30 events, hard cap for object budget
            params = {"apiKey": self.api_key, "leagueID": league, "oddsAvailable": "true"}
            if cursor:
                params["cursor"] = cursor
            r = requests.get(self.BASE, params=params, timeout=20)
            r.raise_for_status()
            body = r.json()
            calls += 1
            raw.append(body)
            for ev in body.get("data", []):
                events_seen += 1
                status = ev.get("status", {})
                if status.get("started") or status.get("completed"):
                    continue  # pregame only — see docstring
                home = canonical_team(ev["teams"]["home"]["names"]["long"])
                away = canonical_team(ev["teams"]["away"]["names"]["long"])
                commence = canonical_ts(datetime.fromisoformat(
                    status["startsAt"].replace("Z", "+00:00")))
                # SGO groups odds by market key like 'points-away-game-ml-away'.
                ml_by_side = {}
                for odd_key, odd in ev.get("odds", {}).items():
                    if odd.get("betTypeID") != "ml" or odd.get("periodID") != "game":
                        continue
                    side = odd.get("sideID")
                    if side not in ("home", "away"):
                        continue
                    ml_by_side[side] = odd.get("byBookmaker", {})
                if "home" not in ml_by_side or "away" not in ml_by_side:
                    continue
                books_both_sides = set(ml_by_side["home"]) & set(ml_by_side["away"])
                for book_raw in books_both_sides:
                    for side, outcome in (("home", home), ("away", away)):
                        entry = ml_by_side[side][book_raw]
                        if not entry.get("available"):
                            continue
                        try:
                            price = american_to_decimal(entry["odds"])
                        except (ValueError, KeyError):
                            continue
                        upd = canonical_ts(datetime.fromisoformat(
                            entry["lastUpdatedAt"].replace("Z", "+00:00")))
                        book_c = canonical_book(book_raw)
                        rows.append({
                            "snapshot_batch_id": batch, "pulled_at": pulled_at,
                            "book_last_update": upd, "sport": sport,
                            "event_id": ev["eventID"], "commence_time": commence,
                            "home_team": home, "away_team": away, "book": book_c,
                            "provider": self.name, "outcome": outcome,
                            "price_decimal": price,
                        })
                        if entry.get("deeplink"):
                            deeplinks.append((ev["eventID"], book_c, outcome,
                                              entry["deeplink"], pulled_at))
            cursor = body.get("nextCursor")
            if not cursor:
                break
        return rows, {"raw": raw, "batch": batch, "pulled_at": pulled_at,
                      "remaining": "n/a", "calls": calls, "events_seen": events_seen,
                      "deeplinks": deeplinks, "league": league}
    
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
    conn = connect()
    providers: list[OddsProvider] = []
    if os.environ.get("ODDS_API_KEY"):
        providers.append(TheOddsApiProvider(os.environ["ODDS_API_KEY"], cfg["regions"]))
    sgo_leagues = cfg.get("sportsgameodds_leagues", [])
    _sgo_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "")
    print(f"SGO gate: key_present={bool(_sgo_key)} key_len={len(_sgo_key)} leagues={sgo_leagues}")
    if _sgo_key and sgo_leagues:
        providers.append(SportsGameOddsProvider(_sgo_key, sgo_leagues))
        print("SGO provider LOADED")
    else:
        print("SGO provider SKIPPED")
    if not providers:
        raise SystemExit("no provider keys configured")

    day = utc_now_str()[:10]
    for provider in providers:
        for sport in cfg["sports"]:
            try:
                rows, meta = provider.fetch_odds(sport)
            except Exception as e:
                print(f"WARN {provider.name} {sport}: {e}")
                continue
            if not rows:
                continue
            inserted = insert_rows(conn, rows)
            append_jsonl(meta, f"{provider.name}_{sport}")
            # provider_usage accounting (our own quota tracking, since SGO has none)
            calls = meta.get("calls", 1)
            events = meta.get("events_seen", len({r["event_id"] for r in rows}))
            conn.execute(
                """INSERT INTO provider_usage (provider, date, calls, events)
                   VALUES (?,?,?,?)
                   ON CONFLICT(provider, date) DO UPDATE SET
                     calls = calls + excluded.calls,
                     events = events + excluded.events""",
                (provider.name, day, calls, events))
            # deep links (SGO only for now)
            for ev_id, book, outcome, url, updated in meta.get("deeplinks", []):
                conn.execute(
                    """INSERT INTO book_deeplinks (event_id, book, outcome, url, updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(event_id, book, outcome) DO UPDATE SET
                         url = excluded.url, updated_at = excluded.updated_at""",
                    (ev_id, book, outcome, url, updated))
            conn.commit()
            print(f"{provider.name}/{sport}: {inserted} new rows, {events} events")

if __name__ == "__main__":
    main()
