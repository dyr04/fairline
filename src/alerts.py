"""Implements plan §4.7/T12: the go/no-go Discord card.

Alerts fire ONLY for my_books, ONLY in the live edge window
[live_bet_edge_min, max_edge], with a hard expiry and a verify-live-price
instruction — an expired or tolerance-failed signal bet anyway is a donation
to the book. Called by the scan job (never by the dashboard)."""
from __future__ import annotations

import os
from datetime import timedelta

import requests

from src.db import canonical_ts, parse_ts, utc_now_str
from src.staking import human_round, kelly_stake, price_floor
import urllib.parse

SUPPRESS_MIN = 30


def should_alert(sig, config: dict, conn) -> bool:
    if sig.status != "active":
        return False
    if sig.book not in (config.get("my_books") or []):
        return False
    if not (config["live_bet_edge_min"] <= sig.edge <= config["max_edge"]):
        return False
    key = f"{sig.event_id}|{sig.book}|{sig.outcome}"
    row = conn.execute("SELECT last_alerted FROM alert_log WHERE key=?", (key,)).fetchone()
    if row:
        age = (parse_ts(utc_now_str()) - parse_ts(row[0])).total_seconds() / 60.0
        if age < SUPPRESS_MIN:
            return False
    return True


def build_card(sig, config: dict, conn=None) -> tuple[str, str]:
    now = parse_ts(utc_now_str())
    expiry = canonical_ts(now + timedelta(minutes=config["alert_expiry_minutes"]))
    tol = config["execution_price_tolerance"]
    floor = price_floor(sig.fair_prob, config["live_bet_edge_min"])
    bankroll = float(config.get("bankroll") or 0)
    stake_line = "set bankroll in config for sizing"
    if bankroll > 0:
        st = human_round(kelly_stake(sig.fair_prob, sig.price_decimal, bankroll,
                                     config["kelly_fraction"]),
                         config["stake_increment"])
        stake_line = f"stake: ${st:.0f} (¼-Kelly, human-rounded)"
    deeplink, link_tier = _resolve_deeplink(sig.event_id, sig.book, sig.outcome,
                                            sig.home_team if hasattr(sig, "home_team") else None,
                                            sig.away_team if hasattr(sig, "away_team") else None,
                                            config, conn)
    tier_note = {"precise": "🔗 Open betslip",
                "sport_page": "🔗 Open sport page",
                "search": "🔍 Find this game"}.get(link_tier)
    link_line = f"\n[{tier_note}]({deeplink})" if deeplink else ""
    headline = f"🎯 **+EV** · {sig.outcome} · {sig.book.upper()} @ **{sig.price_decimal:.3f}**"
    card = (
        f"{headline}\n"
        f"edge **{sig.edge:+.1%}** | fair p={sig.fair_prob:.3f} | {stake_line}\n"
        f"model gives up below **{floor:.3f}** (math floor)\n"
        f"skip if book now shows below **{sig.price_decimal - tol:.3f}** (slippage tolerance)\n"
        f"hard expiry {expiry}"
        f"{link_line}"
    )
    return card, expiry

def _resolve_deeplink(event_id: str, book: str, outcome: str, home: str | None,
                      away: str | None, config: dict, conn) -> tuple[str | None, str | None]:
    """Three-tier deep link (plan: Tier 1 precise > Tier 2 sport-page > Tier 3 search).
    Tier 1 only used if fresh (within staleness_sync_minutes of now) — a stale
    precise link pointing at an old market is worse than a fresh generic one.
    """
    if conn is not None:
        row = conn.execute(
            """SELECT url, updated_at FROM book_deeplinks
               WHERE event_id=? AND book=? AND outcome=?""",
            (event_id, book, outcome)).fetchone()
        if row:
            url, updated_at = row
            sync_limit = float(config.get("staleness_sync_minutes", 5)) * 60.0
            age = abs((parse_ts(utc_now_str()) - parse_ts(updated_at)).total_seconds())
            if age <= sync_limit:
                return url, "precise"

    fallback = (config.get("book_deeplinks_fallback") or {}).get(book)
    if fallback:
        return fallback, "sport_page"

    if home and away:
        q = urllib.parse.quote(f"site:{book}.com {away} vs {home} moneyline")
        return f"https://www.google.com/search?q={q}", "search"
    return None, None

def build_arb_card(arb: dict, config: dict) -> str:
    """Cross-book arbitrage alert. Only fires for POSITIVE margin arbs whose
    involved books are ALL in my_books — an arb you can't execute both legs of
    is a research artifact, not a bet."""
    import json
    books = json.loads(arb["books"])
    lines = []
    for book, outcome, price in books:
        lines.append(f"  · {book.upper()} · {outcome} @ **{price:.3f}**")
    return (
        f"⚡ **ARBITRAGE** · margin **{arb['margin']:+.2%}** · "
        f"event {arb['event_id'][:12]}\n"
        + "\n".join(lines) +
        f"\n⚠️ execute BOTH legs simultaneously or don't bet — "
        f"single-leg exposure kills the guarantee"
    )


def send(sig, config: dict, conn, webhook: str | None = None) -> bool:
    if not should_alert(sig, config, conn):
        return False
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        print("WARN no DISCORD_WEBHOOK_URL; alert skipped")
        return False
    card, expiry = build_card(sig, config, conn)
    requests.post(webhook, json={"content": card}, timeout=10).raise_for_status()
    key = f"{sig.event_id}|{sig.book}|{sig.outcome}"
    conn.execute("INSERT OR REPLACE INTO alert_log (key, last_alerted) VALUES (?,?)",
                 (key, utc_now_str()))
    conn.execute("UPDATE signals SET alert_expiry=? WHERE event_id=? AND book=? "
                 "AND outcome=? AND snapshot_batch_id=?",
                 (expiry, sig.event_id, sig.book, sig.outcome, sig.snapshot_batch_id))
    conn.commit()
    return True


def send_arb(arb: dict, config: dict, conn, webhook: str | None = None) -> bool:
    """Alert on genuine cross-book arbs whose books are all in my_books."""
    import json
    if arb["margin"] <= 0:
        return False
    my = set(config.get("my_books") or [])
    books_in_arb = {b for b, _, _ in json.loads(arb["books"])}
    if not books_in_arb.issubset(my):
        return False
    key = f"arb|{arb['event_id']}|{'-'.join(sorted(books_in_arb))}"
    row = conn.execute("SELECT last_alerted FROM alert_log WHERE key=?", (key,)).fetchone()
    if row:
        age = (parse_ts(utc_now_str()) - parse_ts(row[0])).total_seconds() / 60.0
        if age < SUPPRESS_MIN:
            return False
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return False
    requests.post(webhook, json={"content": build_arb_card(arb, config)},
                  timeout=10).raise_for_status()
    conn.execute("INSERT OR REPLACE INTO alert_log (key, last_alerted) VALUES (?,?)",
                 (key, utc_now_str()))
    conn.commit()
    return True
