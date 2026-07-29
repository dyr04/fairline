# TASKS — fair-line engine

Working checklist and ownership tracker. **Claim a task before starting it** by putting
your name and `in-progress` next to it. Update status when it changes. This prevents two
people building the same thing.

**Status values:** `todo` · `claimed (name)` · `in-progress (name)` · `in-review (name)` · `done`

## How to update this file (example)
```
- [ ] T7 Dashboard pages 1–2 ........... in-progress (Colin)   ← claimed & working
- [x] T13 Multi-provider ingestion ..... done                  ← finished & merged
```
Edit the line, commit it on your task branch, and it merges with your PR — so the tracker
always reflects reality.

---

## Done (built & working)
- [x] T1  Scaffold, config, tooling ................. done
- [x] T2  Poller + provider adapter ................. done
- [x] T3  Devig (multiplicative + power, n-outcome) . done
- [x] T4  Signal engine + arb scanner ............... done
- [x] T5  GitHub Actions cron (poll/scan/results) ... done
- [x] T6  Results ingestion ......................... done
- [x] T13 Multi-provider (SGO) + canonical mapping .. done
- [x] T12 Discord alerts (go/no-go cards) ........... done
- [x] --- Median-outlier guards (signals + arbs) .... done
- [x] --- Three-tier deep links in alert cards ...... done (Tier 3 search not yet active)
- [x] --- provider_usage + book_deeplinks tables .... done

## In progress
- [ ] (nothing claimed yet — Colin: claim T7 below)

## Ready to build now (no waiting on data)
- [ ] T7  Streamlit dashboard pages 1–2 (live board + history) ..... todo  [Colin's first task]
- [ ] T7b Dashboard deploy to Streamlit Community Cloud ............ todo  (after pages render)
- [ ] --- Tier 3 deep-link search fallback: thread home/away team names
        through SignalRow so the search URL can be built ........... todo
- [ ] --- Alert polish: confirm arb cards render + fire on real arbs  todo
- [ ] --- peek.py improvements: signal-age column, per-book edge
        distribution, top-of-day signal history .................. todo

## Blocked until ~1 week of data (resolved games needed)
- [ ] T15 Learning loop (src/learn.py): Brier-score closing lines,
        EWMA+shrinkage into book_sharpness, consensus reads learned
        weights at ≥30 games/book ................................. blocked (needs data)
- [ ] T8  Real backtest run (lag sweep, CLV tiers, EV buckets) ..... blocked (needs ~100 games)
- [ ] T9  Dashboard pages 3–4 (backtest + model health) ........... blocked (needs T8)
- [ ] --- Week-2 data audit: count consensus-quoted events, project
        signal yield, decide if more sports/time needed ........... blocked (needs data)

## Gated (do NOT start — require validation evidence first)
- [ ] T14 Exchange execution engine (Phase 6) — requires positive
        lag-table + 150 forward paper signals + 1mo manual profit .. gated

## Config / ops housekeeping (either of us, anytime)
- [ ] Set branch protection: 1 required approval, squash-only merges,
        require conversation resolution ........................... todo
- [ ] Verify all my_books fallback URLs land correctly ............ done (hardrock, bovada, fanatics ok)
- [ ] Decide whether to add a 3rd sport (soccer for 3-way math test) todo
