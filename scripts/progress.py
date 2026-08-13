"""Quick progress check toward populating the backtest/model-health pages.
Run: python -m scripts.progress"""
from src.db import connect

c = connect()
r = c.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
o = c.execute("SELECT COUNT(DISTINCT event_id) FROM odds_snapshots").fetchone()[0]
both = c.execute(
    """SELECT COUNT(*) FROM game_results g
       WHERE EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.event_id=g.event_id)"""
).fetchone()[0]
has_sharp = c.execute(
    "SELECT name FROM sqlite_master WHERE name='book_sharpness'").fetchone()
scored = 0
if has_sharp:
    scored = c.execute("SELECT COALESCE(SUM(n_games),0) FROM book_sharpness").fetchone()[0]

print("=" * 55)
print("PROGRESS TOWARD BACKTEST / MODEL-HEALTH PAGES")
print("=" * 55)
print(f"  resolved games (results):        {r}")
print(f"  distinct games with odds:        {o}")
print(f"  OVERLAP (backtestable games):    {both}   <-- the number that matters")
print(f"  book-game scores logged:         {scored}")
print("-" * 55)
if both == 0:
    print("  status: no overlap yet. Games resolving now finished")
    print("  BEFORE their odds were captured, OR odds are for")
    print("  games not yet played. Overlap grows as games you're")
    print("  polling NOW finish over the coming days.")
elif both < 50:
    print(f"  status: {both} backtestable — building. ~50 needed for")
    print("  a first meaningful backtest, ~100 to trust it.")
else:
    print(f"  status: {both} backtestable — enough to run:")
    print("  python -m src.backtest   (populates pages 3-4)")
print("=" * 55)

print("\nDIAGNOSTIC: same game in both tables?")
# Find a recent result and look for its teams in odds_snapshots
res = c.execute("SELECT event_id, winner FROM game_results LIMIT 3").fetchall()
for ev_id, winner in res:
    in_odds = c.execute(
        "SELECT COUNT(*) FROM odds_snapshots WHERE event_id=?", (ev_id,)).fetchone()[0]
    # also check if that winner's name appears in ANY odds row (different id)
    elsewhere = c.execute(
        "SELECT COUNT(DISTINCT event_id) FROM odds_snapshots WHERE outcome=?",
        (winner,)).fetchone()[0]
    print(f"  result {ev_id[:10]} winner={winner[:20]:20} "
          f"| same-id odds rows: {in_odds} | that team in odds under any id: {elsewhere}")

print("\nDIAGNOSTIC 2: recoverable via team+date matching")
# How many results could match an odds game on teams + same calendar date?
recoverable = c.execute("""
    SELECT COUNT(DISTINCT g.event_id) FROM game_results g
    WHERE EXISTS (
        SELECT 1 FROM odds_snapshots o
        WHERE substr(o.commence_time,1,10) = substr(g.ingested_at,1,10)
          AND (o.home_team = g.winner OR o.away_team = g.winner)
    )
""").fetchone()[0]
print(f"  results potentially recoverable by team+date: {recoverable} / {r}")
print("  (if this is high, the event-key fix rescues them tonight;")
print("   if low, it's mostly a coverage/timing gap and waiting is the fix)")

print("\nDIAGNOSTIC 3: date ranges of each table")
odds_dates = c.execute(
    "SELECT MIN(substr(commence_time,1,10)), MAX(substr(commence_time,1,10)) FROM odds_snapshots").fetchone()
res_dates = c.execute(
    "SELECT MIN(substr(ingested_at,1,10)), MAX(substr(ingested_at,1,10)) FROM game_results").fetchone()
print(f"  odds cover game dates:    {odds_dates[0]}  ->  {odds_dates[1]}")
print(f"  results recorded on:      {res_dates[0]}  ->  {res_dates[1]}")