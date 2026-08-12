# Onboarding — Colin

Welcome. This gets you from zero to a working local copy of the fair-line engine,
then explains how we work in parallel without stepping on each other.
Do the steps in order; don't skip the verification checkpoints.

---

## Part 1 — Read first (20 min, before touching code)

1. **`devig-arb-project-plan.md`** — the whole system. Read §1 (thesis), §3 (schema),
   §4 (the math), §7 (tickets + anti-drift rules), §9 (pitfalls), §10 (current state).
   §10 tells you exactly where the project is right now.
2. **`DECISIONS.md`** — the shared-memory contract. Every interface decision (schema,
   function signatures, config keys, naming) lives here. Read it before writing any code,
   and append to it whenever you make an interface decision. Agents and humans share no
   memory except this file.

The one-sentence version: we pull odds from multiple books, strip the vig to get true
probabilities, form a consensus fair line, flag prices that beat it, and are building
toward acting on the validated ones with real money — behind hard honesty gates.

---

## Part 2 — Local setup (30 min)

You are setting up your OWN copy on your OWN machine. You are not sharing Dylan's folder
or keys — you clone the repo and use your own API keys.

1. **Clone the repo:**
   ```
   git clone https://github.com/dyr04/fairline.git
   cd fairline
   ```

2. **Create and activate a virtual environment** (Python 3.11+; 3.14 works):
   ```
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1        # Windows PowerShell
   # source .venv/bin/activate          # Mac/Linux
   ```
   Your prompt should now show `(.venv)`.

3. **Install dependencies (including the dev tools):**
   ```
   pip install -r requirements.txt
   pip install pytest ruff
   ```

4. **Get your OWN free API keys** (do NOT use Dylan's — separate keys = separate free
   quotas, which is the whole point):
   - The Odds API: https://the-odds-api.com  → free tier → copy key
   - SportsGameOdds: https://sportsgameodds.com → free tier → copy key

5. **Create your local `.env`** (it's gitignored — it never gets committed):
   ```
   Copy-Item .env.example .env
   ```
   Open `.env`, fill in:
   ```
   ODDS_API_KEY=your_own_odds_api_key
   SPORTSGAMEODDS_API_KEY=your_own_sgo_key
   DISCORD_WEBHOOK_URL=            # leave blank unless testing alerts locally
   ```

---

## Part 3 — Verify your environment matches (5 min)

This is the handshake. If these pass, your setup is identical to Dylan's.

```
python -m pytest -q          # expect: 33 passed
python -m ruff check .       # expect: All checks passed!
```

Then confirm the pipeline runs end to end (uses ~4 of your credits):
```
python -m src.poller         # expect: rows pulled from both providers
python -m src.scan           # expect: {"new_signals": N, "arb_rows": M}
python -m scripts.peek       # expect: labeled signal/filter/arb output
```

If all of that works, you're fully onboarded. If anything fails, screenshot the exact
error and send it to Dylan before proceeding — do not "fix" environment issues by
editing shared code.

---

## Part 4 — How we work in parallel (READ THIS)

### The three branches

- **`main`** — the source of truth. Protected: nobody pushes to it directly. Code only
  reaches `main` through a reviewed pull request. This is always the known-good version.
- **`data`** — machine-owned. The GitHub Actions cron writes gzipped odds snapshots here
  3×/day. **Never touch this branch by hand** — manual commits collide with the bot and
  corrupt the dataset. Read-only for humans.
- **your own branches** — where you actually work. One branch per task, named
  `colin/<task>` (e.g. `colin/dashboard-page3`). You commit freely here, push it, open a
  pull request into `main`, Dylan reviews and merges.

### The workflow, every time you start a task

```
git checkout main
git pull                              # get the latest merged work
git checkout -b colin/my-task         # branch off main
# ... do the work, commit as you go ...
git push -u origin colin/my-task      # push your branch
# then open a Pull Request on GitHub, request Dylan's review
```

After it's merged, delete the branch and start the next one from a fresh `main`.

### The rules that keep us from breaking each other

1. **Claim a whole ticket, not a file.** Tickets (T7 dashboard, T10 steam detector, etc.)
   were designed to touch different files. Check `TASKS.md`, claim your ticket there,
   work only within that ticket's files.
2. **Never both edit the same module at once.** If your ticket and Dylan's both need
   `signal_engine.py`, coordinate first — one goes, then the other rebases.
3. **One approval before merge.** Every PR needs the other person's review. This catches
   mistakes cheaply.
4. **Read/append `DECISIONS.md`** for any interface change.
5. **Run the relevant `scripts/verify_tN.py` before opening a PR.** A ticket isn't done
   until its verify script exits 0.
6. **Don't run the cron locally.** Local runs are for testing only. The scheduled polling
   is GitHub's job; double-polling wastes both our quotas.

### Your first task

**T7 — the Streamlit dashboard** (pages 1–2 to start). It's self-contained (touches only
`dashboard/`), reads the database the cron is already filling, and doesn't collide with
anything Dylan is editing on the alerts/signal path. See `TASKS.md` and plan §4.8 / §7 T7.
