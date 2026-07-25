"""T12: fast CLI to log a live/paper bet (internal or external source).
Usage: python scripts/log_bet.py --source oddsjam --book fanduel --outcome "Las Vegas Aces" \
       --price 2.05 --stake 25 [--fair 0.52] [--achieved 2.02]"""
import argparse

from src.db import connect, utc_now_str

p = argparse.ArgumentParser()
for a, t in [("--source", str), ("--book", str), ("--outcome", str),
             ("--price", float), ("--stake", float)]:
    p.add_argument(a, type=t, required=True)
p.add_argument("--fair", type=float)
p.add_argument("--achieved", type=float)
p.add_argument("--signal-id", type=int)
a = p.parse_args()
conn = connect()
conn.execute("""INSERT INTO live_bets (signal_id, signal_source, book, outcome,
                signal_or_quoted_price, achieved_price, fair_prob_at_log, staked, ts)
                VALUES (?,?,?,?,?,?,?,?,?)""",
             (a.signal_id, a.source, a.book, a.outcome, a.price, a.achieved,
              a.fair, a.stake, utc_now_str()))
conn.commit()
print("logged")
