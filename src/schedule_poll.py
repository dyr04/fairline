"""Implements plan §7 T5 enhancement: dynamic pre-game polling.

Instead of fixed clock times, read each upcoming game's commence_time from the
DB and decide whether we're inside its pre-game capture window (default: poll
if any game commences in the next `pregame_window_min` minutes). This is what
makes closing-line capture reliable — a snapshot lands shortly before each
game regardless of when it starts.

The GitHub Actions cron runs this frequently (e.g. every 30 min); this script
decides whether an actual API poll is warranted, so we spend credits only when
a game is approaching."""
from __future__ import annotations

from datetime import timedelta

import yaml

from src.db import connect, parse_ts, utc_now_str


def games_in_window(conn, window_min: int) -> list[dict]:
    """Distinct upcoming games commencing within window_min from now."""
    now = parse_ts(utc_now_str())
    horizon = now + timedelta(minutes=window_min)
    rows = conn.execute(
        """SELECT DISTINCT event_id, commence_time, sport FROM odds_snapshots
           WHERE commence_time > ? AND commence_time <= ?""",
        (utc_now_str(), horizon.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchall()
    return [{"event_id": r[0], "commence_time": r[1], "sport": r[2]} for r in rows]


def should_poll_now(conn, cfg: dict) -> bool:
    """True if any game is inside its pre-game window OR it's a daily wide poll
    slot (we still want a few coverage polls/day to discover new games)."""
    window = int(cfg.get("pregame_window_min", 25))
    if games_in_window(conn, window):
        return True
    # Fallback wide-poll: always poll if the last snapshot is > max_gap_min old,
    # so brand-new games get discovered even before they enter a pre-game window.
    last = conn.execute("SELECT MAX(pulled_at) FROM odds_snapshots").fetchone()[0]
    if not last:
        return True
    gap = (parse_ts(utc_now_str()) - parse_ts(last)).total_seconds() / 60.0
    return gap >= int(cfg.get("max_gap_min", 240))


def main() -> None:
    cfg = yaml.safe_load(open("config.yaml"))
    conn = connect()
    if should_poll_now(conn, cfg):
        print("POLL")  # workflow checks this output
    else:
        print("SKIP")


if __name__ == "__main__":
    main()