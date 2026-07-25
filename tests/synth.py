"""Synthetic snapshot builders shared by engine/backtest/sharpness tests."""
from datetime import UTC, datetime, timedelta

from src.db import canonical_ts

T0 = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def row(book, outcome, price, batch="b1", pulled_min=0, upd_min=0,
        event="ev1", commence_min=120, sport="basketball_wnba"):
    return {
        "snapshot_batch_id": f"{batch}-{book}" if batch.startswith("auto") else batch,
        "pulled_at": canonical_ts(T0 + timedelta(minutes=pulled_min)),
        "book_last_update": canonical_ts(T0 + timedelta(minutes=pulled_min + upd_min)),
        "sport": sport, "event_id": event,
        "commence_time": canonical_ts(T0 + timedelta(minutes=commence_min)),
        "home_team": "A", "away_team": "B", "book": book,
        "provider": "the_odds_api", "outcome": outcome, "price_decimal": price,
    }


def market(book, pa, pb, **kw):
    """Two rows (both outcomes) for one book in one batch."""
    return [row(book, "A", pa, **kw), row(book, "B", pb, **kw)]


def five_book_event(soft_price_a=2.30, **kw):
    """Pinnacle fair 2.00/2.10-ish + 4 soft books; one soft book offers value on A."""
    rows = []
    rows += market("pinnacle", 1.95, 1.95, batch="p1", **kw)
    rows += market("draftkings", 2.05, 1.87, batch="d1", **kw)
    rows += market("fanduel", 2.02, 1.90, batch="f1", **kw)
    rows += market("betmgm", 2.00, 1.92, batch="m1", **kw)
    rows += market("caesars", soft_price_a, 1.70, batch="c1", **kw)
    return rows
