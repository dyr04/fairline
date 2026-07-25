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


def build_card(sig, config: dict) -> tuple[str, str]:
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
        stake_line = f"stake: ${st:.0f} (human-rounded quarter-Kelly)"
    card = (
        f"**{sig.book.upper()}** {sig.outcome} @ **{sig.price_decimal:.3f}**\n"
        f"edge {sig.edge:+.1%} | fair p={sig.fair_prob:.3f} | {stake_line}\n"
        f"bettable down to **{floor:.3f}** | HARD EXPIRY {expiry}\n"
        f"VERIFY LIVE PRICE — do not bet below {sig.price_decimal - tol:.3f}"
    )
    return card, expiry


def send(sig, config: dict, conn, webhook: str | None = None) -> bool:
    if not should_alert(sig, config, conn):
        return False
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        print("WARN no DISCORD_WEBHOOK_URL; alert skipped")
        return False
    card, expiry = build_card(sig, config)
    requests.post(webhook, json={"content": card}, timeout=10).raise_for_status()
    key = f"{sig.event_id}|{sig.book}|{sig.outcome}"
    conn.execute("INSERT OR REPLACE INTO alert_log (key, last_alerted) VALUES (?,?)",
                 (key, utc_now_str()))
    conn.execute("UPDATE signals SET alert_expiry=? WHERE event_id=? AND book=? "
                 "AND outcome=? AND snapshot_batch_id=?",
                 (expiry, sig.event_id, sig.book, sig.outcome, sig.snapshot_batch_id))
    conn.commit()
    return True
