"""Read-only snapshot of what the engine currently thinks, with plain-English
labels so the output is self-explanatory months from now.

Run: python -m scripts.peek
"""
from src.db import connect

SEP = "=" * 78

REASONS = {
    None: "ACTIVE — passed every filter",
    "below_threshold": "edge too small to act on (< edge_threshold in config)",
    "max_edge": "edge suspiciously LARGE — likely stale line/bad data, not free money",
    "price_sanity": "price outside 1.05–30.0 — a data artifact, not a real quote",
    "overround": "book's margin outside sane band — its devig can't be trusted",
    "stale_sync": "this book's price is much older than the others — not comparable",
    "min_books": "too few books quoting this game to form a consensus",
    "no_anchor": "anchor book didn't quote this game (anchor mode only)",
    "consensus_outlier": "most books disagreed with consensus — the CONSENSUS was wrong",
}

def show_event(conn, event_prefix):
    """Show all snapshots for events whose id starts with a given prefix.
    Helps diagnose cross-provider staleness and price artifacts."""
    print()
    print(SEP)
    print(f"EVENT DIAGNOSTIC — all rows for event(s) starting '{event_prefix}'")
    print(SEP)
    rows = list(conn.execute(
        "SELECT event_id, book, provider, outcome, price_decimal, book_last_update "
        "FROM odds_snapshots WHERE event_id LIKE ? "
        "ORDER BY event_id, book, book_last_update",
        (event_prefix + "%",)))
    if not rows:
        print("(no rows found)")
        return
    for r in rows:
        print(f"  {r[0][:12]}  {r[1]:<14} {r[2]:<15} {r[3]:<22} "
              f"price={r[4]:<6.2f}  updated={r[5]}")

conn = connect()

print(SEP)
print("ACTIVE SIGNALS — prices the model currently believes are +EV")
print("  price     = decimal odds offered ($100 stake returns $100 x price)")
print("  fair      = model's estimate of the TRUE win probability")
print("  book      = what this book's price implies, after removing its vig")
print("  edge      = fair - book, in probability points. +3.7% means you're")
print("              paid as if the team wins 30% when the market says 34%.")
print(SEP)
rows = list(conn.execute(
    "SELECT book, outcome, price_decimal, fair_prob, edge FROM signals "
    "WHERE status='active' ORDER BY edge DESC LIMIT 15"))
if not rows:
    print("(none — no +EV opportunities right now. This is the NORMAL result.)")
for book, outcome, price, fair, edge in rows:
    print(f"  {book:<13} {outcome:<24} price={price:<6.2f} "
          f"fair={fair:.3f}  book={fair - edge:.3f}  edge={edge:+.2%}")

print()
print(SEP)
print("FILTER OUTCOMES — where every evaluated price ended up")
print("  A healthy run is MOSTLY rejections. Few or zero active signals is")
print("  the expected result in an efficient market, not a failure.")
print(SEP)
total = 0
for reason, n in conn.execute(
        "SELECT rejection_reason, COUNT(*) FROM signals "
        "GROUP BY rejection_reason ORDER BY COUNT(*) DESC"):
    total += n
    label = REASONS.get(reason, reason or "?")
    print(f"  {n:>5}  {label}")
print(f"  {total:>5}  TOTAL prices evaluated")

print()
print(SEP)
print("ARBITRAGE / MARKET TIGHTNESS")
print("  margin = 1 - (sum of implied probs at the BEST price for each side).")
print("  POSITIVE margin = true arbitrage: betting both sides guarantees profit.")
print("  NEGATIVE margin = NOT a bet. It measures how tight the market is:")
print("  -0.22% means the best cross-book prices are only 0.22% away from a")
print("  riskless trade. These near-misses are logged on purpose — the")
print("  distribution of them IS the market-efficiency finding (plan 4.2).")
print(SEP)
arbs = list(conn.execute(
    "SELECT event_id, margin, books FROM arb_events ORDER BY margin DESC"))
if not arbs:
    print("  (no arb or near-arb windows recorded yet)")
for event_id, margin, books in arbs:
    kind = "*** TRUE ARB ***" if margin > 0 else "near-miss (not bettable)"
    print(f"  {margin:+.2%}  {kind}")
    print(f"         event {event_id[:12]}  {books}")


# Diagnostic: inspect the three "arb" events that looked suspicious
for prefix in ("ce049001", "a9c8aad6", "71a4c184"):
    show_event(conn, prefix)