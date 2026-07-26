"""Verify T13: multi-provider ingestion, canonical mapping, usage logging."""
import sys

from src.db import connect

conn = connect()
providers = [r[0] for r in conn.execute(
    "SELECT DISTINCT provider FROM odds_snapshots")]
if len(providers) < 2:
    sys.exit(f"FAIL: only {providers} providers in odds_snapshots; expected >=2")

# Non-canonical names would fragment the sharpness table (§3 reconciliation).
bad_books = [r[0] for r in conn.execute(
    "SELECT DISTINCT book FROM odds_snapshots WHERE book != LOWER(book) OR book LIKE '% %'")]
if bad_books:
    sys.exit(f"FAIL: non-canonical book names: {bad_books}")

# Cross-provider overlap: at least one book should appear from both providers.
overlap = list(conn.execute(
    """SELECT book, COUNT(DISTINCT provider) n FROM odds_snapshots
       GROUP BY book HAVING n >= 2"""))
if not overlap:
    sys.exit("FAIL: no book quoted by both providers — mapping likely broken")

usage = list(conn.execute("SELECT provider, date, calls, events FROM provider_usage"))
if not usage:
    sys.exit("FAIL: provider_usage empty")

deeplinks = conn.execute("SELECT COUNT(*) FROM book_deeplinks").fetchone()[0]
print(f"verify_t13: OK — {len(providers)} providers, "
      f"{len(overlap)} books cross-quoted, {len(usage)} usage rows, "
      f"{deeplinks} deep links stored")