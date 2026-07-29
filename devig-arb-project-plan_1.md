# Fair-Line & Arbitrage Detection Engine — Full Build Plan
**A live multi-book betting-market pricing and execution system.**
*Working build document. Owners: Dylan & Colin. Any contributor (human or agent) should be able to execute each phase from this spec alone. This is a real system intended to identify and act on genuine +EV and arbitrage opportunities — not a demo.*

---

## 1. Project Thesis (the elevator pitch)

Sports betting markets are fragmented: dozens of books independently price the same binary outcome. By (a) removing each book's margin ("vig") to recover implied probabilities, (b) anchoring on the sharpest book (Pinnacle) as the consensus fair price, and (c) scanning for cross-book price dislocations, we can measure market efficiency and detect +EV and arbitrage opportunities — the same logic as cross-exchange price discovery and stat-arb in financial markets.

**Core discipline:** this is a market-efficiency *measurement* engine first and a betting tool second — in that order, on purpose. Arbs detected via polled API data are frequently stale by the time they're flagged, so the system's job is to measure the frequency, size, and lifetime of real edges, prove they survive execution lag, and only then act on them. Money is made by acting on validated edges, not by trusting the backtest. The honesty machinery (lag sweep, CLV tiers, forward-test gate) exists precisely because real capital is on the line.

**Sport choice:** WNBA + MLB now (both in season, live data flowing). Architecture is sport-agnostic; NBA plugs in when its season starts, and MLB's ~15 games/day is the fastest path to a mature book-sharpness table.

---

## 2. Tech Stack (and why each choice)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Industry standard for quant research; rich ecosystem |
| Data source | The Odds API (`us` + `eu` regions) | Multi-book aggregation in one call; `eu` region is required because Pinnacle (the sharp anchor) is not returned in `us` |
| Storage | SQLite via `sqlite3`/SQLAlchemy | Zero-config, single-file DB that ships inside the repo; time-series odds snapshots are append-only and small. Upgrade path to Postgres is one connection string — mention this |
| Scheduler | GitHub Actions cron | Free, cloud-based (laptop can be off), and the commit history itself proves the data was collected in real time — an auditable dataset |
| Analysis | pandas + numpy | Standard |
| Dashboard | Streamlit + Plotly | Pure Python, free hosting on Streamlit Community Cloud → live shareable URL, no install; renders live computations (repricer) that a BI tool can't |
| Backtest | Custom vectorized pandas engine | Off-the-shelf backtesters (backtrader etc.) are equity-centric; per-bet event studies are simpler and show you understand the math |
| Code quality | VS Code, `ruff` lint, type hints, `pytest` on the math modules | Portfolio polish; tests on devig/arb math are cheap and impressive |
| Secrets | `.env` + GitHub Actions secrets | Never commit the API key |

**Vendor independence stance:** data vendors are commodity inputs; the project's value is the models and measurement layer, never the feed. The poller is built behind a provider adapter (T2), and the ACTIVE plan is legitimate free-tier composition: **one account per vendor across multiple vendors** (The Odds API first; a second aggregator such as OddsPapi or SportsGameOdds added in T13 — verify each vendor's current free-tier terms at signup) **plus direct exchange trading APIs** (official, first-party, free for reasonable use). This multiplies free data coverage with zero terms violated. Multi-accounting on any single vendor is permanently out of scope, as is direct scraping of sportsbooks: books have no official APIs, aggregators exist precisely to maintain fleets of ToS-violating scrapers behind proxy pools, and rebuilding that is a company, not a feature. Multi-vendor lands AFTER the MVP (T13, Phase 2) — the MVP runs single-vendor so entity-resolution complexity never delays Day 1–3.

**API credit budget (critical constraint):**
- Free tier = 500 credits/month. One odds pull = `regions × markets` credits → `us`+`eu`, h2h only = **2 credits/pull**.
- **Polling strategy (reviewer-informed): concentrate the budget where edges are actionable.** Lines posted hours before tipoff have already been picked over by faster participants; a manual bettor's latency is a smaller fraction of edge lifetime near tipoff. Cadence: 3 sparse wide polls/day (open-line history, steam detection, edge-by-time analysis need them) + dense polling from 75 to 15 minutes before each scheduled tipoff — every 5 min on the $30 tier, every 15–20 min on free.
- The last dense pre-tipoff pull doubles as the closing-line capture (**the one dataset that is never sacrificed**; when credits run low, drop daytime polls first — see P9).
- If arb-window measurement needs more density, the $30/mo tier (20K credits) funds the 5-min pre-tipoff cadence across 2–3 sports. Recommended for one month during data collection; cancel after. This is the single highest-value dollar spent in the project.

---

## 3. Architecture

```
GitHub Actions (cron)
   └── poller.py ──> The Odds API (us+eu, h2h)
          └── raw JSON snapshot ──> SQLite: odds_snapshots
                                        │
        ┌───────────────────────────────┤
        ▼                               ▼
  devig.py (fair probs,           arb_scanner.py
  Pinnacle anchor)                (cross-book best-price scan)
        │                               │
        ▼                               ▼
  signals table                   arb_events table
        │                               │
        └────────────┬──────────────────┘
                     ▼
        Streamlit dashboard (live view + history)
                     ▼
        backtest.py (ROI, Sharpe, hit rate, CLV, drawdown)
```

### Database schema (SQLite)

```sql
-- CANONICAL TIMESTAMP FORMAT (mandatory, all tables, all writers):
-- datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') — UTC, no microseconds, Z suffix.
-- SQLite compares these as strings; ONE row in another format silently corrupts ordering,
-- closing-line derivation, and arb-window durations. verify_t2/t6 regex-check every timestamp.

-- one row per book per outcome per snapshot
CREATE TABLE odds_snapshots (
  id INTEGER PRIMARY KEY,
  snapshot_batch_id TEXT NOT NULL,  -- one UUID per poller invocation per sport; the ONLY valid
                                    -- grouping key for devig (see grouping contract below)
  pulled_at TEXT NOT NULL,          -- canonical UTC format above
  book_last_update TEXT NOT NULL,   -- the book's own last_update from the API — REQUIRED, see P2
  sport TEXT NOT NULL,              -- 'basketball_wnba'
  event_id TEXT NOT NULL,           -- Odds API event id
  commence_time TEXT NOT NULL,
  home_team TEXT, away_team TEXT,
  book TEXT NOT NULL,               -- CANONICAL book id ('pinnacle', 'draftkings', ...) via mapping module
  provider TEXT NOT NULL DEFAULT 'the_odds_api',  -- which feed supplied this row (multi-vendor, T13)
  outcome TEXT NOT NULL,            -- team name
  price_decimal REAL NOT NULL
  -- CROSS-PROVIDER RECONCILIATION: the same book may arrive from two providers.
  -- Consumers always take, per (event, book), the row with the freshest book_last_update,
  -- regardless of provider. Book and team names MUST pass through the canonical
  -- mapping module before insert — 'Pinnacle' vs 'pinnacle' fragments the sharpness table.
  -- NOTE: no is_closing flag. "Closing" is DERIVED at query time as the last
  -- snapshot per (event, book) with pulled_at < commence_time — robust to cron jitter (see P3),
  -- with a staleness quality flag (see §4.3).
);
CREATE INDEX ix_event ON odds_snapshots(event_id, pulled_at);
CREATE UNIQUE INDEX ux_row ON odds_snapshots(event_id, book, outcome, snapshot_batch_id);

-- GROUPING CONTRACT: devig consumes ALL outcomes of one market together. Group ONLY by
-- (event_id, book, snapshot_batch_id) — never by pulled_at, which can mix vintages after
-- partial re-inserts. Every consumer asserts the group contains exactly the expected N
-- outcomes for the market (2 for basketball h2h) and SKIPS incomplete groups with a log line.

CREATE TABLE signals (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,          -- canonical format
  snapshot_batch_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  sport TEXT NOT NULL,
  book TEXT NOT NULL,                -- the soft book offering the price
  outcome TEXT NOT NULL,
  price_decimal REAL NOT NULL,       -- the soft book's price at signal time
  fair_prob REAL NOT NULL,           -- anchor/consensus fair probability
  edge REAL NOT NULL,
  model TEXT NOT NULL DEFAULT 'A',   -- 'A' (live) ; 'B' exists only in backtest replay
  status TEXT NOT NULL,              -- 'active' | 'rejected'
  rejection_reason TEXT              -- 'max_edge'|'overround'|'min_books'|'stale_sync'|'no_anchor'|NULL
);
CREATE UNIQUE INDEX ux_signal ON signals(event_id, book, outcome, snapshot_batch_id);

CREATE TABLE game_results (
  event_id TEXT PRIMARY KEY,
  winner TEXT, home_score INT, away_score INT
);
```

Results ingestion: The Odds API `/scores` endpoint (cheap).

---

## 4. The Math (module specs — implement exactly, with tests)

### 4.1 `devig.py`
Input: list of decimal odds for the N outcomes of one market at one book. **Write every function n-outcome from day one** (two for basketball, three for soccer 1X2) — the math is identical, and hardcoding 2 outcomes forces a Phase 5 rewrite.

- Implied prob (raw): `p_raw = 1 / decimal_odds`. Sum > 1; the excess is the **overround/vig**.
- **Multiplicative (proportional) method — the baseline:** `p_fair_i = p_raw_i / Σ p_raw`. Simple, standard, but known bias: it under-corrects longshots (favorite–longshot bias).
- **Power method — the comparison:** find k such that `Σ p_raw_i^k = 1`, then `p_fair_i = p_raw_i^k`. Solve with `scipy.optimize.brentq`. Handles longshot bias better.
- Implement both; the dashboard/backtest use multiplicative by default with power as a config flag. Being able to explain WHY two devig methods disagree is a genuinely important distinction to understand. (Mention Shin's method exists for insider-trading-adjusted markets; do not implement — out of scope.)
- **Fair line anchor:** devigged Pinnacle price = consensus "true" probability. Why Pinnacle: it welcomes sharp action, runs low margins (~2–3%), and its closing line is the academic benchmark for market efficiency (this is why CLV is measured against it).
- **Edge signal:** for each soft book: `edge = p_fair_pinnacle − p_implied_book_fair`. Positive edge above a threshold (start 2%) ⇒ +EV flag written to `signals`.
- **Signal-quality filters (apply BEFORE writing any signal — industry practice per OddsJam/Monahan methodology):**
  - *Max-EV cap:* discard edges > 8%. Extreme edges are almost always stale lines, data errors, or unpriced news — adverse selection, not alpha. (An outlier that looks too good IS the information that something is wrong.)
  - *Overround sanity (per-book bands):* require two-sided quotes. Anchor (Pinnacle) overround must be in [1.5%, 8%] or the fair line is untrustworthy → skip the event for Model A. Soft books use a wider band [1.5%, 15%] — many US books run 8–12% vig on WNBA and excluding them wholesale would discard legitimate targets (reviewer finding).
  - *Market depth:* require ≥ 4 books quoting the event before flagging any edge or arb; thin markets manufacture fake gaps.
  - *Staleness sync (uses book_last_update):* only compare two books' prices if their `book_last_update` values are within 5 minutes of each other; otherwise the "edge" is a timestamp artifact, not a price disagreement. Apply to both +EV signals and arb detection.
  - *Anchor-missing rule:* if Pinnacle has no quote for an event, Model A emits NO signal for that event (log reason='no_anchor'). Never silently substitute another book as the anchor.
  - Log filtered-out signals with a rejection reason — the rejection stats themselves go in the writeup.

Tests: two-outcome devig sums to 1; power method reproduces multiplicative when vig≈0; known hand-computed example.

### 4.2 `arb_scanner.py`
For each event snapshot: take best (highest) decimal price per outcome across all books.
- `arb_margin = 1 − (1/best_home + 1/best_away)`. If > 0 ⇒ arbitrage.
- Stake split for total stake S: `stake_i = S × (1/odds_i) / Σ(1/odds_j)` → equal guaranteed payoff.
- Log every arb event: books involved, margin, first-seen, last-seen (⇒ **window duration** — a headline result: "median arb window lasted X minutes with median margin Y%").
- Also log near-arbs (margin > −1%) — they show how tight the market is.

### 4.3 `backtest.py`
Strategy simulated: **bet the +EV signal** (soft book price above Pinnacle fair prob, threshold sweep 1–5%).
- Stake sizing: flat 1-unit AND **fractional Kelly (¼ Kelly)**: `f* = (b·p − q)/b`. Report both. Why fractional: full Kelly is optimal in expectation but assumes p is known exactly; estimation error makes full Kelly wildly over-aggressive — ¼ Kelly sacrifices growth for drawdown control. (This is position sizing under parameter uncertainty.)
- Metrics:
  - **ROI** = net profit / total staked
  - **Hit rate** vs breakeven rate implied by avg odds
  - **Per-bet Sharpe** = mean(bet returns)/std(bet returns); note honestly that annualizing is ill-defined for irregular bet arrival — report per-bet and per-100-bets. Knowing the limitation > pretending it away.
  - **Max drawdown** on the cumulative unit P&L
  - **Execution-lag sensitivity (THE FIRST metric reported, before all others):** the naive backtest fills at signal-fire price — a price a manual bettor never touches. Sweep `execution_lag_minutes` ∈ {0, 1, 3, 5, 10}: reprice every simulated bet at the best available snapshot ≥ lag minutes after signal-fire (skip the bet if none before tipoff). Report ROI and CLV at each lag as the headline table. If the edge is gone at 3 minutes, no real capital deploys until lag-adjusted CLV is positive — every other result is downstream of this table.
  - **Signal staleness exclusion:** compute `signal_staleness = pulled_at − book_last_update` for the signaling book on every bet; exclude staleness > 10 min from primary ROI/CLV and report the excluded fraction. If >30% of signals are stale at entry, the polling cadence — not the model — is the binding constraint; say so in the writeup.
  - **CLV**: your entry price vs devigged Pinnacle *closing* price. **Closing-line quality tiers (mandatory):** compute `minutes_before_commence` for each derived closing snapshot; tier as `tight` (<20 min), `acceptable` (20–45 min), `stale` (>45 min). Report CLV separately per tier; `stale` closes are EXCLUDED from headline CLV (cron jitter means the "last snapshot" can be a mid-afternoon line masquerading as the close). Report the tier distribution in the writeup. **Why CLV is the primary metric:** small-sample ROI is dominated by variance; beating the closing line is the accepted proof of skill because the close is the market's most efficient price. A profitable backtest with negative CLV = luck; positive CLV with flat ROI = skill not yet realized.
  - **Expected vs realized P&L**: plot cumulative Σ(EV per bet) against cumulative realized profit on one chart. If realized oscillates around expected → edges are real and gaps are variance; persistent divergence below expected → edges were fake. This chart is more diagnostic than the equity curve alone.
  - **Edge by time-to-tipoff**: bucket signals by hours before commence_time and report edge frequency/size/CLV per bucket — early lines are wider and softer; quantify it.
  - **EV-bucket calibration (validates the max-EV cap empirically):** run the backtest with the 8% cap DISABLED, group all signals by promised edge (0–2%, 2–4%, 4–8%, 8%+), and compare promised EV vs realized ROI per bucket. **Out-of-sample discipline (reviewer finding):** setting the cap on the same data that measured the buckets is in-sample selection. Split games chronologically — first 60% calibrates the cap, last 40% validates it holds; report both folds. If the sample is too small to split meaningfully (<150 signals), state that the cap is indicative only and must be re-confirmed on forward paper-traded signals before real staking. Whatever the data says, the production cap is a measured parameter with a stated validation status, never an assumption.
- Bias controls (non-negotiable, they protect real capital): no look-ahead (signals only use data timestamped before bet), bets priced at the snapshot price actually observed, results joined after the fact.

### 4.5 `sharpness.py` — learned book-weighting consensus (Model B)
Two fair-line models run side by side, and the backtest decides which wins:
- **Model A (baseline):** devigged Pinnacle = fair probability (§4.1).
- **Model B (learned consensus):** fair probability = weighted average of ALL books' devigged closing probs, weights learned per (book, sport) from historical accuracy. This is **forecast combination** (Bates & Granger 1969): weight forecasters by demonstrated skill.

**Sharpness scoring (Brier):** for each completed game and book b, score the devigged closing prob p against outcome y∈{0,1}: `BS = (p − y)²`. Lower = sharper. **Exchanges participate as forecasters too:** an exchange's implied probability is the back/lay midpoint (commission-adjusted if only one side is quoted), not a devig — flagged via the `exchange_books` config so the pipeline applies the right transform. Their weight is then earned empirically like any book's; if the exchange order book is as sharp as its reputation, the Brier table will say so. Maintain per (book, sport) an exponentially weighted average: `S_b ← λ·S_b + (1−λ)·BS`, λ = 0.98 (≈ 50-game memory). Why EWMA: books improve or decay; recent accuracy matters more, and new sports/books phase in automatically.

**Shrinkage (critical at small n):** raw per-book Brier over <200 games is mostly noise. Shrink toward the cross-book mean: `S_shrunk = (n_b·S_b + n0·S̄) / (n_b + n0)` with n0 = 30 pseudo-games. New books enter at exactly the average weight and earn deviation from it. Why: estimation error — same principle as ¼ Kelly. Unshrunk weights would chase luck.

**Weights:** `w_b ∝ 1/S_shrunk`, normalized to sum to 1 per sport. Persist the sharpness table to a `book_sharpness` table (book, sport, S_shrunk, n_games, updated_at) — this table IS the empirical answer to "which books are sharp at which sports" and is a headline output of the system.

**Leave-one-out (avoids circularity):** when computing book X's edge, build the consensus EXCLUDING book X's own line — otherwise X's price partially cancels its own measured gap.

**Known limitation (state it, don't hide it):** books are correlated (soft books copy sharps), so a weighted mean overweights the herd; inverse-covariance weighting is the documented next step, out of scope for v1.

**Evaluation:** backtest Model A, Model B, and **Model H (hybrid — reviewer-proposed production mode)** on identical bet universes: H uses Model A's anchor when Pinnacle quoted both sides within 30 minutes of tipoff, and falls back to Model B's consensus otherwise — covering the no_anchor dead zones that are common in thin Pinnacle WNBA coverage without abandoning the anchor where it's reliable. H is evaluated as a third variant; it does not replace the clean A-vs-B comparison. **CRITICAL — walk-forward weights only:** for every simulated bet, Model B's (and H's fallback) weights must be computed exclusively from games resolved BEFORE that bet's timestamp, updating as the simulation advances. Computing weights over the full dataset and then backtesting on the same data is look-ahead bias — Model B would "win" fraudulently. The T11 verify script must include a test proving weights at time t are invariant to data after t. The interesting outcome either way: if B beats A, learned weighting adds information beyond Pinnacle; if A beats B, Pinnacle already dominates the aggregate — both are publishable results.

**Why B is not guaranteed to beat A (important — don't assume it):** in the limit of infinite clean data, if Pinnacle truly dominates, learned weights would converge onto Pinnacle and B would collapse into A. In finite real samples that doesn't hold: (1) weights are estimated with error even after shrinkage; (2) soft books largely copy Pinnacle with a lag, so averaging correlated forecasters can pull the consensus toward a shared blind spot rather than diversifying away noise — this is the documented "forecast-combination puzzle" in the forecasting literature, where sophisticated weighted combinations often lose to a single strong benchmark precisely because of estimation error. This is exactly why the plan backtests both instead of assuming B wins — never trust the more sophisticated model just because it is more sophisticated.

### 4.7 `alerts.py` and live execution tracking — making signals actually actionable
A Streamlit page you have to check isn't real-time; a usable signal has to reach you.
- **Alerting (Discord) — the go/no-go card, not a generic ping:** each alert contains: signal price, fair prob, edge, signal age in minutes, a HARD EXPIRY timestamp (`signal_time + 4 min`, stored as `alert_expiry` on the signal row), the instruction line: "VERIFY LIVE PRICE — do not bet if the book now shows worse than [signal_price − execution_price_tolerance]", **and a tap-to-open deep link into the book's app/site** (config `book_deeplinks: {book: url_template}` — shaves 30–60s off every manual execution, the cheapest latency win in the system). Tolerance is a config key (default 0.04 decimal). An expired or tolerance-failed signal that gets bet anyway is a donation to the book; the card exists to make skipping easy.
- **Alert gate ≠ research threshold:** signals are LOGGED from the research threshold (2%) but ALERTED only in the live window `live_bet_edge_min` (3.5%) to `max_edge` (8%): after 2–4 minutes of human latency, 2–3% edges mostly arrive at the book as zero-or-negative EV, while 3.5–6% edges retain cushion. Alerts also fire ONLY for books in `my_books`; everything else is research.
- **Exchange handling:** betting exchanges (commission-based, peer-matched) don't limit winners — sharp action is their business model — making them the durable long-term venue. Their prices are near-fair already: adjust for commission instead of devigging (effective price = quoted price net of commission on winnings). Flag exchange books in config with their commission rate. **Liquidity audit before any real exchange capital (reviewer finding):** manually record depth at the best three levels on five consecutive WNBA games; if average matched depth < $200/side, exchanges leave the WNBA live path and return with NBA (Phase 5), where liquidity is materially deeper — thin books' back/lay spreads eat the entire modeled edge at small size.
- **Account sequencing (operational, not a footnote):** retail US books commonly limit winning accounts within roughly 50–200 sharp bets — often faster on low-volume sports like WNBA — so treat retail account capacity as a depleting resource. Sequence: forward-test and first real stakes on venues that tolerate winners (exchanges where liquid, offshore within the risk cap below); bring retail accounts into rotation only after forward-tested CLV is confirmed, and log `account_health` (book, date, max_bet_observed, restriction_flag) from the first real bet so capacity decay is measured, not discovered.
- **Offshore exposure cap:** config `max_offshore_fraction: 0.30` — offshore balances are uninsured, withdrawal is discretionary, and terms are broadly written; never hold more than 30% of total betting bankroll across offshore books, sweep withdrawals whenever the ceiling is exceeded, and treat those balances as a separate risk bucket in any P&L accounting.
- **Stake sizing (`src/staking.py`, pure functions):** computed stake = quarter-Kelly (`kelly_fraction` × bankroll × f*) using the frozen `fair_prob` from the signal row. **Human-unit rounding:** round to the nearest natural increment (`stake_increment`, default $5; use $25 above $250) — calculator-exact stakes like $98.34 are a profiling tell. Tie-break DOWN, and never let rounding exceed the computed stake by more than 5%: the Kelly curve is asymmetric, so overbetting costs more than underbetting saves, especially under edge-estimation error. **Price floor on every alert:** the card includes the worst live decimal price at which the bet still clears `live_bet_edge_min` given the frozen fair_prob — a one-glance "bettable down to X, stake $Y."
- **Repricer (dashboard page 5):** select an active signal, input the live price your book is actually showing → instant recomputed edge, go/no-go verdict, and human-unit stake (pure client-side math on the frozen fair_prob). This is the pivot tool for when the line moves during your reaction window — you adjust the bet instead of abandoning the opportunity or, worse, betting the stale numbers.
- **External signal sources (e.g. an OddsJam subscription) — the system as auditor:** `live_bets` and forward paper logs carry a `signal_source` tag ('internal' | 'oddsjam' | ...). Opportunities found on a commercial screen are MANUALLY logged (your own betting record — clean); automating off their dashboard violates subscriber terms — programmatic access is their separately licensed API product, a purchase decision, never a scraping shortcut. Page 5 reports realized-vs-promised EV and lag-adjusted CLV PER SOURCE: the system independently grades whether the commercial feed's advertised edges realize at your latency, on your books — a number no subscriber otherwise has. Sizing, human-unit rounding, price floor, and the repricer apply identically to external signals; the discipline layer is source-agnostic.
- **Execution slippage tracking:** the backtest assumes you transact at the exact snapshot price — you never will. The `live_bets` table (signal price, actually-achieved price, time-to-execute) measures your personal slippage and folds it honestly into forward performance; it is also the empirical check on the lag-sweep assumptions in §4.3.
- **Forward paper-trading gate (hard, quantified):** after the historical backtest, log live signals unstaked and score them exactly as in backtest. Real staking begins only after **≥150 forward signals with positive lag-adjusted CLV**. A backtest alone is a hypothesis; the forward test is the validation.
- **Bankroll floor (economics, stated plainly):** at ~2% net edge, ~1.85 avg odds, a few WNBA bets/day, quarter-Kelly on a $1,000 bankroll expects under $1/day — negative return on time. Below roughly $3,000–5,000 of dedicated bankroll, real staking is an empirical exercise (slippage measurement), not an income strategy; the README says this in exactly those terms.

### 4.8 `dashboard.py` (Streamlit)
- Page 1 — Live board: matrix of games × books, moneylines side by side (OddsJam-style), devigged Pinnacle fair prob column, +EV cells highlighted, arb banner if active.
- Page 2 — History: pick a date/game → line-movement chart per book (Plotly), arb events table.
- Page 3 — Backtest: equity curves for Model A vs Model B, metrics table, threshold sensitivity chart, and the book_sharpness table rendered as a heatmap (books × sports).
- Page 4 — Model Health (the "how good is the model" page): cumulative expected-vs-realized P&L chart with divergence shading; rolling CLV over time; calibration plot (predicted probability deciles vs actual win frequency — a well-calibrated model hugs the diagonal); EV-bucket promised-vs-realized table; signal rejection stats by filter reason. This page is the fastest way to see, at a glance, whether the model is actually working.
- Page 5 — Live/Paper Tracker: active signals above threshold, forward-logged (unstaked) bets with running CLV, and once real betting starts, logged slippage (signal price vs achieved price) alongside backtested assumptions.
- Deploy to Streamlit Community Cloud reading the SQLite file from the repo (Actions commits keep it fresh).

---

## 5. Roadmap

### Phase 0 — Setup (Day 0, ~2 hrs)
- [ ] Repo scaffold, venv, `.env`, Odds API key, `ruff` + `pytest` config
- [ ] Confirm Pinnacle appears in `eu` region for `basketball_wnba`

### Phase 1 — 3-Day MVP
**Day 1:** `poller.py` (fetch → normalize → SQLite), run manually, verify schema. `devig.py` multiplicative + tests.
**Day 2:** `arb_scanner.py` + tests; GitHub Actions cron (hourly window + pre-tipoff closing pull); results ingestion.
**Day 3:** Streamlit pages 1–2; deploy; README v1.
**MVP exit criteria:** cloud pipeline logging real WNBA odds unattended; dashboard shows live fair lines and flags arbs/+EV.

### Phase 2 — Data accumulation (Weeks 1–4, passive)
- Pipeline runs itself; weekly sanity check (row counts, missed closing lines).
- **Week 2 data audit (reviewer finding — Pinnacle's WNBA coverage may be thin):** count events where Pinnacle quoted both sides; compute signal yield rate; project whether ~100 usable signals arrive before Phase 3's planned start. Fallback criterion: if <50 Pinnacle-quoted events by Week 4, extend collection (add NBA preseason in October via config) before running Phase 3, and say so in the writeup rather than backtesting a too-small sample.
- Optional: $30 tier for 5-min polling density during ~2 weeks for arb-window stats.
- Add power-method devig during this window.

### Phase 3 — Backtest & metrics (Week 3–4, once ~100+ games logged)
- `backtest.py` full implementation, threshold sweep, Kelly vs flat, CLV report, EV-bucket calibration.
- `sharpness.py`: Brier scoring, shrunk weights, Model B consensus; backtest A vs B head-to-head (§4.5).
- Dashboard pages 3–4 (backtest + Model Health).

### Phase 4 — Polish & writeup (Week 4–5)
- 1-page writeup: thesis → method → data (n games, n snapshots) → results (arb window stats, CLV, ROI, expected-vs-realized, sharpness table) → limitations → what you'd do next (spreads/totals, player props, live NBA, execution latency study).
- Limitations section must state: main-market moneylines on major leagues are the sharpest markets in sports; real-world edges concentrate in props, alt lines, and minor leagues. Moneyline scope was a deliberate data-cost decision, and the architecture extends to any market The Odds API carries.
- README with architecture diagram, live dashboard link, sample charts.

### Phase 5 — Market expansion (post-validation; gated on Phase 3/4 results)
Only begins once the moneyline engine shows positive CLV / clean calibration — validating on the SHARPEST market first is the credibility play; then point the proven engine at markets where edges actually concentrate:
- **Expansion sequencing (architecture is sport-agnostic; these are the operational realities):** WNBA now → **MLB next** (≈15 games/day makes it the fastest sharpness-table builder in sports — 50-game per-sport weight maturity arrives in under a week) → NBA in October → **EPL/soccer** (exercises the n-outcome 3-way math and directly tests the Euro-books-sharpness hypothesis) → **NFL** (deepest liquidity, sharpest lines — true hard mode, and only ~16 games/week so validation is slow) → **tennis** (high volume, but confirm results-settlement coverage before enabling — The Odds API's scores endpoint varies by sport) → **golf last**: outright markets with 100+ runners and enormous overrounds are a different market structure entirely (this is where the power devig method matters most), requiring its own market-type extension.
- Per-sport scaling constraints to budget for: API credits scale linearly with sports polled; Model B needs ~50+ resolved games per sport before shrinkage lets weights deviate from the mean; results coverage must be verified per sport before it enters the config.
- **Minor-league & international moneylines (cheap first step):** config-only additions (e.g. `soccer_epl`, `cricket_ipl`, lower-tier basketball). Directly tests the "which books are sharp at which sports" hypothesis with real geographic variety; sharpness table grows automatically.
- **Alternate lines & spreads/totals:** same two-outcome devig math, new market keys; multiplies API credit cost per pull — budget before enabling. This unlocks **middles** (opposing bets at different numbers — e.g. over 162.5 / under 165.5 — winning both when the result lands between, losing only vig otherwise): the existing cross-book best-price scan plus a line-gap detector, a natural sibling of the arb scanner.
- **Player props (the real soft market):** biggest true edges, but three new problems: (1) each prop market is billed separately — credit costs jump an order of magnitude, so poll selectively; (2) Pinnacle's prop coverage is thin → Model A weakens and Model B's learned weights become the primary fair line (this is where Model B earns its keep); (3) settlement needs player box scores — **do not use basketball-reference/Sports Reference for this.** Their terms of use explicitly prohibit automated scraping and explicitly bar building tools from their data ("you should not create websites or tools based on data you scrape from Sports Reference"). Use a licensed source instead (e.g. balldontlie.io's free NBA/WNBA box-score API, or a stats endpoint from your odds provider) — evaluate options when Phase 5 actually starts.
- Exit criterion for the project narrative: "validated on hard mode, deployed on soft markets."

### Phase 6 — Automated execution (exchange APIs only; gated on Phase 3–4 + the forward gate + live manual results)
Human latency is the system's largest single profit leak, so automation is the logical endgame — but the venue decides whether it's buildable:
- **Retail sportsbooks: never automated.** They have no trading APIs; automating them means botting consumer apps through bot-detection, violating their terms, and risking voided accounts with confiscated balances. This stays permanently in /forbidden.
- **Exchanges: the legitimate path.** Exchanges publish real trading APIs designed for programmatic order placement — the sports equivalent of a brokerage API. Phase 6 = an execution engine against the exchange(s) that passed the §4.7 liquidity audit.
- **Architecture principle: deterministic engine, no AI in the order path.** Models decide what is mispriced; a dumb, fast, hard-limited engine places orders. No LLM judgment between signal and order. Hard controls, all in config, all enforced in code: `max_stake_per_bet`, `max_daily_loss` (engine halts), `max_open_exposure`, per-market liquidity minimum (skip if depth < N× stake), and a manual kill switch (a file/flag checked before every order). Every order and every skip is logged with its triggering signal id.
- **Entry gates, all required:** backtest lag-table positive at realistic lags → ≥150 forward paper signals with positive lag-adjusted CLV → ≥1 month of profitable-or-flat MANUAL exchange execution with logged slippage → then automation, starting at minimum stakes with the daily-loss halt set tight.
- Ticket (T14) to be spec'd only when the gates are passed — writing it now would be scope creep on an unvalidated system.

### Cut list (if time slips) — in cut order
1. Steam-move detector (T10 — nice-to-have, not core)
2. Power-method devig (keep multiplicative only)
3. Kelly sizing (keep flat stakes)
Never cut: closing-line capture, CLV, tests on the math.

---

## 6. Design Rationale (one line per major decision — the why behind each choice)

- - Pinnacle's devigged close as the fair-price benchmark because sharp, low-margin books are the accepted efficiency baseline — CLV against that close is how professionals separate skill from variance."
- "I implemented two devig methods and can explain when they disagree — proportional devig under-corrects longshots, which is the favorite–longshot bias."
- "I framed arbitrage as market-microstructure measurement: median window duration and margin size, because polled data can't prove executability — that honesty matters more than a flashy claim."
- "I built my own time-series dataset with a cloud cron pipeline; the git commit history is an audit trail proving no look-ahead."
- "I sized simulated positions with fractional Kelly because full Kelly assumes zero estimation error in win probability — quarter Kelly trades growth for drawdown control."
- "My primary success metric is Closing Line Value, not ROI, because at n<1,000 bets ROI is noise."
- "I validated the pipeline on WNBA in-season, sport-agnostic by design, so NBA is a config change."
- "Per-bet Sharpe rather than annualized, because bet arrival is irregular — I'd rather report a correct number than an impressive one."
- "I built a forecast-combination model: every book's devigged close is Brier-scored per sport, and consensus weights are learned from that skill — then I ran it head-to-head against the Pinnacle anchor and let CLV pick the winner."
- "I shrank per-book sharpness estimates toward the cross-book mean with a 30-game pseudo-count, because at my sample size raw weights would chase noise — the same estimation-error logic as fractional Kelly."
- "I cap the maximum edge I'll signal at 8%, because an outlier that looks too good usually IS the information that something is wrong — stale line, data error, or unpriced news. That's adverse selection."
- "My key diagnostic chart is cumulative expected profit vs realized profit — if they track, gaps are variance; if they diverge, the edges were never real. It separates model failure from bad luck."
- "I didn't assume the learned-weight model would beat the Pinnacle-anchor model just because it's more sophisticated — that's actually a known failure mode in forecasting, the forecast-combination puzzle, where weight-estimation error and correlated inputs make simple benchmarks hard to beat. I backtested both and let CLV decide."
- "I treat the backtest as a hypothesis, not a validation — before any real money, signals get forward-logged unstaked for weeks to confirm the edge survives out-of-sample, and I track my own execution slippage against the backtested fill price."
- "People ask how this differs from commercial tools like OddsJam: those sell signals as a black box — I built the engine that generates them from raw data, then went past it: learned per-sport book weighting they don't offer, and an audit layer measuring whether promised edges actually realize. A subscription proves nothing about me; the engine does."

---


## 7. Agent Handoff — Task Tickets

Each ticket is a self-contained agent role. `/goal` = the verifiable end state (how you check it's done without trusting the agent's word). `/loop` = the exact prompt to run; repeat the loop until `/goal` verifies.

**Execution order & dependencies:** T1 → T2 → T5 (data flowing ASAP) → T3 → T4 → T6 → T7 (MVP complete) → [data accumulates] → T13 (multi-provider, during Phase 2) → T8 → T11 → T9. T12 any time after T4. T10 optional, last.

**Model routing (token efficiency — match model capability to ticket difficulty):**
- **Haiku-class (cheapest):** T1 scaffold, T6 results ingestion, T10 steam detector, all verify-script boilerplate, README/writeup formatting passes. These are mechanical execution against a fully-specified /steps list; frontier reasoning adds cost, not quality.
- **Sonnet-class (the workhorse — default):** T2 poller, T3 devig, T4 signal engine + arb, T5 Actions workflows, T7 dashboard, T12 alerts/staking, T13 multi-provider. Standard implementation with tight specs and executable verify gates.
- **Frontier-class (Opus/Fable — reserve for bias-sensitive logic):** T8 backtest and T11 sharpness/walk-forward, where subtle look-ahead or grouping errors corrupt results silently and the verify scripts themselves must be adversarially designed; plan-level reviews and red-teams; T14 (execution engine) when it exists.
- **Escalation rule:** any blocker report (standing rule 7) escalates the ticket one model tier before a human gets involved. **Review rule:** bias-critical diffs (anything touching T8/T11 replay, weights, or closing-line logic) written by a cheaper model get one frontier-model review pass before merge — reviewing a diff costs a fraction of writing it and catches the silent failures that matter.

**Standing rules for ALL tickets — the anti-drift protocol:**
1. **Scope lock.** Touch ONLY the files named in your ticket's /steps. If you find a bug elsewhere, write it under "HANDOFF NOTES" in DECISIONS.md and continue — do not fix it, do not refactor it.
2. **Dependency lock.** Approved libraries: requests, pandas, numpy, scipy, streamlit, plotly, pyyaml, pytest, ruff, python-dotenv. Anything else requires a one-line justification appended to DECISIONS.md before use.
3. **Never weaken a test.** If a test fails, fix the code. Deleting, skipping, loosening tolerances, or rewriting assertions to pass is a ticket failure.
4. **Ambiguity rule.** When the spec underdetermines a choice, pick the SIMPLEST interpretation consistent with §4, record it in DECISIONS.md, and continue. Never resolve ambiguity by adding features.
5. **Verify script.** Every ticket ships `scripts/verify_tN.py` with exit code 0/1; the human runs it; only its exit code counts. The verify script may not share logic with the code it verifies (query the DB directly, hit the URL directly).
6. **Shared memory.** Read DECISIONS.md before writing any code; append every interface decision (names, signatures, schema, config keys) when done.
7. **Loop budget.** If the /goal is not met after 3 full loop iterations, STOP. Write a blocker report in DECISIONS.md (what was tried, what failed, suspected cause) instead of hacking around the obstacle.
8. Read §9 before starting. P1–P9 override anything else you believe.

---

**T1 — Scaffold**
/goal: Repo contains `src/{poller,devig,arb_scanner,backtest,sharpness,alerts,db}.py`, `dashboard/`, `tests/`, `scripts/`, `data/`, `config.yaml`, `.github/workflows/poll.yml` (placeholder), `README.md`, `.env.example`, `DECISIONS.md`; `pytest` exits 0 on a placeholder test; `ruff check .` exits 0.
/steps:
1. Read plan §2–3, §9, and the standing rules.
2. **Repo MUST be public** — Streamlit Community Cloud reads the data branch via raw GitHub URLs, which 404 on private repos (silent blank dashboard). Record in DECISIONS.md.
3. Create the exact directory tree above — no extra directories.
4. Each `src/*.py` stub: a module docstring citing its spec section (e.g. "Implements §4.1") and function signatures raising `NotImplementedError`. No logic.
5. `config.yaml`: keys `sports: [basketball_wnba]`, `my_books: []`, `regions: [us, eu]`, `edge_threshold: 0.02`, `live_bet_edge_min: 0.035`, `max_edge: 0.08`, `min_books: 4`, `staleness_sync_minutes: 5`, `execution_price_tolerance: 0.04`, `alert_expiry_minutes: 4`, `max_offshore_fraction: 0.30`, `exchange_books: {}` (name → commission rate), `bankroll: 0`, `kelly_fraction: 0.25`, `stake_increment: 5` — the single source of tunables.
6. `.env.example`: `ODDS_API_KEY=`, `DISCORD_WEBHOOK_URL=`. Requirements file with approved deps only.
7. `DECISIONS.md`: header explaining its contract role + empty "DECISIONS" and "HANDOFF NOTES" sections + the canonical timestamp format from §3 + the data-file naming contract: `raw/{YYYY-MM-DD}_{sport}.jsonl.gz` on the `data` branch, indexed by `manifest.json` at the data-branch root (appended on every T5 commit; T7 discovers files ONLY via the manifest).
8. One placeholder test; run `pytest` and `ruff check .` until both exit 0.
9. Write `scripts/verify_t1.py`: asserts tree exists, both commands exit 0, DECISIONS.md contains the timestamp and naming contracts.
/forbidden: implementing any real logic; extra deps; CI beyond the placeholder workflow file; creating the data branch (T5's job).
/loop: "Read devig-arb-project-plan.md §7 T1 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t1.py; fix until exit 0."

**T2 — Poller**
/goal: `python -m src.poller` twice inserts h2h rows (regions us+eu) into `odds_snapshots` for every sport in `config.yaml`, deduped on (event, book, outcome, pulled_at); every row stores `book_last_update`; ≥1 row has book='pinnacle'; each run also appends the raw API response to `data/raw/{YYYY-MM-DD}_{sport}.jsonl.gz`; remaining-credit headers are logged, warning below 100.
/steps:
1. Read DECISIONS.md, plan §2–3, §9 P2/P4/P9.
2. `src/db.py`: connection helper + idempotent `CREATE TABLE IF NOT EXISTS` for the full §3 schema (including `signals`) + the unique indexes.
3. `src/poller.py`: implement a provider adapter interface — `OddsProvider.fetch_odds(sport) -> list[NormalizedRow]` — with `TheOddsApiProvider` as the first implementation (provider selected by config key `provider: the_odds_api`). All downstream code consumes normalized rows only and never touches provider-specific JSON; this makes data vendors swappable/composable commodities (a second aggregator or a direct exchange API is a new adapter, zero downstream change). For each sport in config, generate ONE `snapshot_batch_id` (uuid4) per sport per invocation; GET `/v4/sports/{sport}/odds?regions=us,eu&markets=h2h&oddsFormat=decimal`. One request per sport per run; on failure, ONE retry after 5s, then log and move on.
4. ALL timestamps written anywhere use the §3 canonical format (`%Y-%m-%dT%H:%M:%SZ`, UTC, no microseconds) — never bare `.isoformat()`, never `utcnow()`.
5. Append the verbatim JSON response (one line, with pulled_at and batch id) to the day's gzipped JSONL — this file is the source of truth per P4; SQLite is derived.
6. Normalize into rows per the §3 schema incl. `snapshot_batch_id` and `book_last_update`; insert with `INSERT OR IGNORE` against the unique index. Post-insert assertion: every (event, book, batch) group has exactly 2 outcomes; incomplete groups are logged and deleted (grouping contract in §3).
7. Read `x-requests-remaining` from response headers; log it; WARNING below 100.
8. Run twice against live data ≥1 minute apart.
9. `scripts/verify_t2.py`: row count grew; a pinnacle row exists; unique index holds; `book_last_update` and `snapshot_batch_id` non-null on 100% of rows; EVERY `pulled_at`, `book_last_update`, `commence_time` matches regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`; no (event, book, batch) group has ≠2 outcomes; JSONL exists and gunzips to valid JSON lines; credit warning fires with a mocked header.
/produces: `data/raw/{YYYY-MM-DD}_{sport}.jsonl.gz` relative to repo root on the current branch (T5 relocates these to the `data` branch; the naming contract lives in DECISIONS.md).
/forbidden: any devig/edge math; scheduling logic; touching dashboard/; polling loops or sleeps (single pass per invocation); more than one retry per request.
/loop: "Read plan §7 T2 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t2.py; fix until exit 0. Append interface decisions to DECISIONS.md."

**T3 — Devig module**
/goal: `src/devig.py` exposes `devig_multiplicative(odds: list[float]) -> list[float]` and `devig_power(odds: list[float]) -> list[float]`, n-outcome, pure; ≥6 unit tests pass.
/steps:
1. Read DECISIONS.md and plan §4.1.
2. Implement both functions for ANY n ≥ 2 outcomes. Validate inputs (all odds > 1.0, len ≥ 2) → raise ValueError otherwise.
3. Power method: solve k with `scipy.optimize.brentq` on bracket [0.5, 3.0]; if no sign change, widen once to [0.1, 10.0], then raise.
4. Docstrings: the math, and why the two methods disagree (favorite–longshot bias).
5. Tests (exactly these six): 2-outcome sums to 1 (both methods); 3-outcome sums to 1 (both); hand-computed multiplicative case (2.10/1.80 → known values); power≈multiplicative when overround < 0.5%; longshot case where power > multiplicative for the favorite; ValueError on odds ≤ 1.
6. `scripts/verify_t3.py`: runs `pytest tests/test_devig.py -q`, asserts ≥6 passed.
/forbidden: any I/O, DB access, or pandas in devig.py; implementing Shin's method; touching any other src module.
/loop: "Read plan §7 T3 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t3.py; fix until exit 0."

**T4 — Arb scanner + signal engine**
/goal: `src/arb_scanner.py` scans the latest complete batch per event, writes `arb_events` (books, margin, first_seen, last_seen) and near-arbs (margin > −1%); `src/signal_engine.py` computes +EV signals per §4.1 (all filters, incl. rejected rows with reasons) and writes the `signals` table — this module is the ONLY writer of signals and is built from pure functions the backtest will reuse; synthetic tests prove a known 1.9% arb, the stake split, and one signal per filter-rejection path.
/steps:
1. Read DECISIONS.md, plan §4.1, §4.2, §9 P2/P8, and the §3 grouping contract.
2. Query: latest complete (event, book, snapshot_batch_id) group per event; DISCARD books whose `book_last_update` is >5 min older than the freshest surviving book on that event (staleness sync).
3. Arb: best price per outcome across surviving books; `arb_margin = 1 − Σ(1/best_price_i)` (n-outcome); stake split `stake_i = S·(1/odds_i)/Σ(1/odds_j)` — pure, unit-tested; upsert arb_events (new combo ⇒ first_seen=last_seen=now; existing ⇒ update last_seen).
4. Signals: `signal_engine.py` exposes pure functions `compute_signals(snapshot_group_rows, config) -> list[SignalRow]` applying §4.1 in order (anchor rule, overround bands, min books, staleness sync, threshold, max-edge cap); every evaluated candidate is returned as active or rejected-with-reason; a thin DB wrapper writes them (INSERT OR IGNORE on the §3 unique index).
5. Tests: synthetic 2.10/2.10 → 4.76% margin; synthetic 1.9% arb with equal-payoff split; no-arb negative; stale-book exclusion; one synthetic case per rejection_reason producing the correct status.
6. `scripts/verify_t4.py`: pytest subset green + one real scan writes arb_events and signals rows (≥0) without error + rejected signals present with non-null reasons.
/forbidden: alerting (T12); modifying devig.py or poller.py; filtering signals by my_books (research layer records ALL books; my_books gates alerts only); any impure DB access inside compute_signals.
/loop: "Read plan §7 T4 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t4.py; fix until exit 0. Append decisions to DECISIONS.md."

**T5 — Actions cron**
/goal: Workflow runs hourly 16:00–04:00 UTC, appends the poller's gzipped JSONL to the `data` branch (never sqlite, never to main); a daily heartbeat job posts to Discord if today's snapshot count is zero or < 25% of the 7-day average; repo shows ≥24h of unattended data-branch commits.
/steps:
1. Read DECISIONS.md (naming contract), plan §2, §9 P3/P4/P9.
2. Create orphan `data` branch containing an empty `manifest.json` (`{"files": []}`).
3. `poll.yml` — exact flow (do not improvise): cron 16:00–04:00 UTC hourly + `workflow_dispatch`. Steps: (a) `actions/checkout` main into the default workspace; (b) install deps, run poller with the API key secret — writes `data/raw/*.jsonl.gz` in the main workspace per T2 /produces; (c) `actions/checkout` the `data` branch into subdirectory `data-branch/` (`with: {ref: data, path: data-branch}`); (d) copy `data/raw/*.jsonl.gz` → `data-branch/raw/`; (e) append each new filename to `data-branch/manifest.json`; (f) commit and push the data branch with pull-rebase retry ×3; (g) run the arb scanner + signal engine so signals/arb_events stay current, and invoke alerts if T12 is installed; **(h) run `python -m src.results` (settle completed games) then `python -m src.learn` (update Brier scores → persist `book_sharpness`) — this closes the learning loop per T15. Nothing about results or weights is ever entered by hand.**
4. `heartbeat.yml`: daily 12:00 UTC; counts yesterday's rows from the data branch; POSTs to Discord (webhook secret) if zero or <25% of the 7-day average; includes the latest credit-remaining figure.
5. Push, trigger manually once, confirm green; then wait for one scheduled run.
6. `scripts/verify_t5.py`: asserts a data-branch commit within the last 2 hours during game windows; `manifest.json` lists every raw file actually present (no orphans either direction); no .sqlite/.env blob tracked anywhere.
/forbidden: committing .sqlite, .env, or any secret; hardcoding keys in YAML; editing poller logic; scheduling more frequently than hourly (credit budget).
/loop: "Read plan §7 T5 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t5.py; fix until exit 0."

**T6 — Results ingestion**
/goal: `game_results` contains winners for all of yesterday's completed games in configured sports; joining to `odds_snapshots` on event_id yields zero orphaned results.
/steps:
1. Read DECISIONS.md, plan §3.
2. `src/results.py`: GET `/v4/sports/{sport}/scores?daysFrom=2`; map completed games to (event_id, winner, home_score, away_score); idempotent upsert.
3. Skip non-final games; log (not insert) games with missing/void status.
4. Orphan check query: results rows with no matching odds_snapshots event_id → must be zero (event IDs come from the same API, so orphans indicate a bug).
5. `scripts/verify_t6.py`: yesterday's completed games present; orphan count = 0; running twice doesn't duplicate; every timestamp column matches the §3 canonical regex.
/forbidden: scraping ANY website (results come only from The Odds API); touching schema beyond game_results; inferring winners from odds.
/loop: "Read plan §7 T6 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t6.py; fix until exit 0."

**T7 — Dashboard pages 1–2**
/goal: A public Streamlit URL renders (1) a live games×books moneyline matrix with devigged Pinnacle fair-prob column, +EV highlights (all §4.1 filters applied), arb banner; (2) a history page with per-book line-movement Plotly chart and arb-events table.
/steps:
1. Read DECISIONS.md, plan §4.1, §4.8, §9 P4.
2. `dashboard/data_loader.py`: fetch `manifest.json` from the data branch raw URL, then fetch exactly the files it lists (never directory-guess), rebuild an in-memory/tmp SQLite, cache with `st.cache_data(ttl=600)`. The dashboard NEVER writes.
3. Page 1: pandas pivot (rows=games, cols=books, values=decimal price); fair-prob column = devig_multiplicative on Pinnacle's pair; highlight cells passing ALL §4.1 filters (threshold, cap, overround band, min books, staleness sync, anchor rule); arb banner when arb_events has an active window.
4. Page 2: date+game selectors; Plotly line chart of each book's price over pulled_at; arb events table for that game.
5. Run locally; then deploy to Streamlit Community Cloud (no secrets needed — data branch is public reads).
6. `scripts/verify_t7.py`: data_loader returns >0 rows; both page modules import clean; deployed URL returns HTTP 200.
/forbidden: building pages 3–5; authentication; writing to any table; recomputing anything the §4.1 filters don't specify; custom CSS/JS beyond Streamlit defaults.
/loop: "Read plan §7 T7 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t7.py; fix until exit 0."

**T8 — Backtest (Model A)**
/goal: `python -m src.backtest` deterministically produces `results/metrics.json` (ROI, hit rate, per-bet Sharpe, max drawdown, avg CLV, % beating close — for flat and ¼-Kelly, across thresholds 1–5%), `results/equity.png`, and the EV-bucket table (cap disabled); a look-ahead test passes.
/steps:
1. Read DECISIONS.md, plan §4.3, §9 P1/P2/P8.
2. Replay: walk snapshot batches chronologically; recompute signals by calling T4's `signal_engine.compute_signals` (the SAME pure functions — never a parallel reimplementation) at each batch's own timestamp; a "bet" is (event, book, outcome, price, ts) at the first batch where the signal fires.
3. Closing line: derive as last pre-commence batch per (event, book); compute `minutes_before_commence`; exclude `closing_line_quality='stale'` (>90 min) closes from CLV per §4.3; Pinnacle's devigged close is the CLV benchmark.
4. Join game_results; settle; compute each metric as its own tested pure function.
5. Kelly: ¼ Kelly with p = devigged Pinnacle prob at bet time.
6. Threshold sweep 1–5% in 0.5 steps; **execution-lag sweep {0,1,3,5,10} min per §4.3 reported as the headline table**; signal-staleness exclusion (>10 min) with excluded fraction reported; EV-bucket run with max_edge disabled, bucketed 0–2/2–4/4–8/8+, cap set on the 60/40 chronological split per §4.3.
7. Look-ahead test: corrupt all post-t snapshots in a copy → bets at ≤ t are byte-identical.
8. `scripts/verify_t8.py`: runs the module twice, asserts identical metrics.json (determinism), asserts look-ahead test green.
/forbidden: ML libraries; tuning then reporting only the best threshold (report the full sweep); using any snapshot after a bet's timestamp for its entry decision; touching Model B (T11).
/loop: "Read plan §7 T8 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t8.py; fix until exit 0. Append decisions to DECISIONS.md."

**T11 — Book-sharpness consensus (Model B)** *(after T8)*
/goal: `src/sharpness.py` maintains `book_sharpness` (book, sport, shrunk Brier, n_games; EWMA λ=0.98, shrinkage n0=30); leave-one-out weighted consensus generates Model B signals with WALK-FORWARD weights only; backtest emits side-by-side A-vs-B metrics JSON on the identical bet universe; tests cover Brier math, cold-start (new book = mean weight), leave-one-out exclusion, and walk-forward invariance.
/steps:
1. Read DECISIONS.md, plan §4.5, §9 P1 — twice.
2. Pure functions first: `brier(p, y)`, `ewma_update(s, bs, lam)`, `shrink(s, n, s_bar, n0)`, `weights(scores)`, `loo_consensus(probs, weights, exclude_book)`.
3. Walk-forward driver: iterate games in commence order; BEFORE each game's bets, weights reflect only games already resolved; AFTER settlement, update that sport's scores.
4. Persist `book_sharpness` after full pass (this table is the writeup exhibit).
5. Extend backtest with `--model {A,B,H}` reusing the T8 replay unchanged (H per §4.5: A's anchor when Pinnacle quoted both sides within 30 min of tipoff, else B's consensus); emit `results/comparison.json` covering all three.
6. Four test classes exactly as named in /goal; invariance test: alter all post-t outcomes → weights at t byte-identical.
7. `scripts/verify_t11.py`: pytest green + comparison.json exists with both models' metrics + invariance test explicitly re-run.
/forbidden: full-sample weights anywhere, even as a "reference"; modifying Model A code paths; inverse-covariance weighting (documented out of scope); tuning λ or n0 to make B win.
/loop: "Read plan §7 T11 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t11.py; fix until exit 0."

**T9 — Dashboard pages 3–4 + writeup** *(after T11)*
/goal: Page 3 renders A-vs-B equity curves, metrics table, threshold sensitivity, sharpness heatmap; page 4 renders expected-vs-realized P&L, rolling CLV, calibration plot, EV-bucket table, rejection stats; a 1-page PDF writeup exists consistent with every §6 talking point.
/steps:
1. Read DECISIONS.md, plan §4.8, §6.
2. Pages read ONLY precomputed artifacts (metrics.json, comparison.json, book_sharpness) — never rerun backtests in the app.
3. Build the charts listed in §4.8, one function per chart.
4. Writeup: thesis → method → data volume → results → limitations (§ Phase 4 list) → next steps; hard 1-page limit; export PDF.
5. Cross-check: table mapping each §6 talking point to the repo artifact or writeup sentence that supports it; include in DECISIONS.md.
6. `scripts/verify_t9.py`: pages import clean; PDF exists and is 1 page; every §6 point appears in the cross-check table.
/forbidden: recomputing metrics in the dashboard; exceeding one page; claiming any result not present in a results/ artifact.
/loop: "Read plan §7 T9 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t9.py; fix until exit 0."

**T12 — Alerting, slippage, paper-trading gate** *(after T4)*
/goal: `src/alerts.py` posts the §4.7 go/no-go card to Discord within seconds of a signal entering the live window (`live_bet_edge_min` ≤ edge ≤ `max_edge`), ONLY for `my_books`, with `alert_expiry` stored on the signal row; duplicates don't re-alert within 30 min; `live_bets` + `account_health` tables and logging CLIs exist; dashboard page 5 shows active signals and forward-tested (unstaked) CLV with 150-signal gate progress; README states the staking gate, bankroll floor, and offshore cap.
/steps:
1. Read DECISIONS.md, plan §4.7, §4.8 page 5.
2. `src/staking.py` per §4.7: pure functions `kelly_stake(fair_prob, price, bankroll, fraction)` and `human_round(stake, increment)` (tie-break down, never >5% above computed) — unit-tested including the $98.34→$95 case and the over-Kelly guard.
3. `alerts.py`: called by the scan job; filter to my_books AND the live edge window; card format per §4.7 (price, edge, signal age, hard expiry timestamp, verify-live-price line with tolerance, **price floor, human-unit stake**); write `alert_expiry` to the signal row; suppress repeats < 30 min.
4. `live_bets` (signal_id NULLABLE for external bets, signal_source, book, outcome, signal_or_quoted_price, achieved_price, fair_prob_at_log, staked, ts) and `account_health` (book, date, max_bet_observed, restriction_flag) tables + tiny CLIs to log both — the external-bet CLI takes source, book, price, and stake so an OddsJam-found bet logs in seconds.
5. Page 5 per §4.8, including a forward-gate progress tile (signals logged / 150, running lag-adjusted CLV), per-source realized-vs-promised EV and CLV breakdown, and the **repricer widget** (§4.7: live-price input → edge, go/no-go, human-unit stake from the frozen fair_prob).
6. README sections: staking gate (≥150 forward signals with positive lag-adjusted CLV), bankroll floor, max_offshore_fraction, account-sequencing summary.
7. `scripts/verify_t12.py`: synthetic in-window my_books signal → real Discord card with expiry, tolerance line, price floor, and rounded stake; below-window (2.5% edge) signal → NO alert but still logged; non-my_books → NO alert; repeat within 30 min → NO alert; staking unit tests green.
/forbidden: any automated bet placement or browser automation against any sportsbook (exchange-API execution is Phase 6, separately gated); alerting books outside my_books; alerting outside the live edge window; alerting from the dashboard (alerts belong to the scan job).
/loop: "Read plan §7 T12 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t12.py; fix until exit 0."

**T10 (optional) — Steam-move detector**
/goal: Dashboard histogram of soft-book lag times behind Pinnacle moves >2% probability within 30 min.
/steps: 1. Read §4.5 context + DECISIONS.md. 2. Detect Pinnacle prob moves >2% within 30 min from snapshots. 3. For each, find each soft book's first subsequent move in the same direction; lag = time delta. 4. Histogram on the dashboard. 5. Manually inspect one real steam event end-to-end; document it in DECISIONS.md. 6. verify_t10.py: synthetic steam sequence produces known lags.
/forbidden: touching signal or backtest logic; new tables beyond steam_events.
/loop: "Read plan §7 T10 and execute its /steps in order. Finish by running scripts/verify_t10.py; fix until exit 0."

**T13 — Multi-provider ingestion + canonical mapping** *(Phase 2, after T7)*
/goal: A second aggregator adapter and one exchange-API adapter run alongside TheOddsApiProvider under the same `OddsProvider` interface; `src/mapping.py` canonicalizes book names, team names, and cross-provider event matching (sport + commence_time ±10 min + normalized teams); rows carry `provider`; consumers resolve duplicates by freshest `book_last_update`; a coverage report shows books-per-vendor overlap; all existing verify scripts still pass untouched.
/steps:
1. Read DECISIONS.md, plan §2 (vendor stance), §3 (reconciliation rule), §9 P7.
2. `src/mapping.py`: canonical book-id table, team-name normalizer, and `match_event(provider_event) -> canonical_event_id` keyed to The Odds API's event ids as the canonical space; unmatched events are logged, never guessed.
3. Sign up for ONE account at the chosen second aggregator (owner does this; verify its free-tier terms at signup and record them in DECISIONS.md). Implement its adapter emitting normalized rows through mapping.py.
4. Implement one exchange adapter (whichever exchange from `exchange_books` has a public API and passed/awaits the §4.7 liquidity audit); back/lay midpoint transform per §4.5.
5. Wire all providers into the poll job (each failure isolated — one vendor down never blocks the others); JSONL raw files gain a provider field.
6. `scripts/coverage_report.py`: books × vendors matrix + rows-per-vendor-per-day — a writeup exhibit showing what free-tier composition bought.
7. `scripts/verify_t13.py`: rows exist from ≥2 providers; zero non-canonical book/team names in the DB; a synthetic cross-provider duplicate resolves to the freshest row; every prior verify script still exits 0.
/forbidden: more than one account at any single vendor; scraping any sportsbook; touching model/backtest code; letting mapping failures insert unmatched events silently.
/loop: "Read plan §7 T13 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t13.py; fix until exit 0. Append the canonical mapping decisions to DECISIONS.md."

**T15 — Live learning loop (settle → score → persist → consume)** *(after T6; the live counterpart of T11)*
/goal: `src/learn.py` runs unattended after each poll: for every newly-settled game it computes each book's devigged CLOSING probability, Brier-scores it against the result, EWMA-updates (λ=0.98) and shrinks (n0=30) per (book, sport), and persists to `book_sharpness`; `_consensus_signals` reads those persisted weights once a (book, sport) has n_games ≥ `min_games_for_learned_weights` (default 30) and otherwise uses the inverse-overround prior; `--dry-run` prints the weight table without writing. **No result or weight is ever entered by hand — this ticket is what makes the model self-improving.**
/steps:
1. Read DECISIONS.md, plan §4.5, §9 P1, and `src/sharpness.py` — the pure functions (`brier`, `ewma_update`, `shrink`, `weights`) already exist; REUSE them, never reimplement.
2. `src/learn.py`: select settled events in `game_results` not yet scored (add a `sharpness_scored` table or a `scored_at` column); for each, derive the closing group per book (last complete pre-commence batch), devig it, `brier()` against the winner, `ewma_update`, then `shrink` toward the cross-book mean; upsert `book_sharpness` (book, sport, shrunk_brier, n_games, updated_at).
3. Idempotency is mandatory: re-running must never double-score a game. Verify by running twice and asserting `book_sharpness` is byte-identical.
4. `signal_engine._consensus_signals`: replace the hardcoded `wts[book] = 1/max(ovr, 0.02)` with a weight resolver — learned inverse-shrunk-Brier when `n_games >= min_games_for_learned_weights`, else the overround prior. Log which source was used per scan so the transition from prior to learned is observable.
5. Config: add `min_games_for_learned_weights: 30`.
6. Wire into `poll.yml` step (h) per T5 so it runs unattended after every poll.
7. `scripts/verify_t15.py`: synthetic settled games produce hand-checkable Brier values; the job is idempotent across two runs; a book below the game threshold still receives the prior weight; a book above it receives the learned weight.
/forbidden: scoring a game before its commence_time (walk-forward violation); hand-entering results or weights anywhere; scoring from non-closing snapshots; letting a quote that failed price_sanity/overround enter the Brier scoring (garbage in the weights is exactly the failure this project already hit once live).
/loop: "Read plan §7 T15 and execute its /steps in order. Do not deviate from /steps or violate /forbidden. Finish by running scripts/verify_t15.py; fix until exit 0. Append decisions to DECISIONS.md."

---

## 8. Risk & Reality Notes (read before betting real money)

- **Books limit winners, on a measurable clock.** Retail US books commonly restrict sharp accounts within ~50–200 winning bets (faster on low-volume sports); treat retail capacity as depleting, sequence venues per §4.7, and log account_health from bet one. Arbing may violate their terms of service.
- **Bankroll floor:** below ~$3,000–5,000 dedicated bankroll, quarter-Kelly staking at realistic edges earns under $1/day in expectation — real staking below that is slippage research, not income.
- **The staking gate is hard:** ≥150 forward paper-traded signals with positive lag-adjusted CLV, or no real money. No exceptions for a good-looking backtest.
- **Stale-line risk is the real killer:** an arb on your dashboard is ~minutes old; betting one leg and losing the other price turns a "riskless" trade into a naked position. Never execute without both live prices confirmed.
- **Legality varies by state/jurisdiction** — verify yours before wagering.
- **Trust the CLV before trusting the ROI.** If backtested CLV isn't clearly positive after 100+ signals, the edge isn't real yet — don't fund it.
- Free-tier polling density understates arb frequency; disclose this in the writeup.

---

## 9. Production Pitfalls — MANDATORY reading for every agent before every ticket

**P1 — Look-ahead in Model B weights (silent, result-corrupting).** Weights must update walk-forward (§4.5). Full-sample weights backtested on the same sample = fraudulent Model B win. Verify: weights at time t invariant to post-t data.

**P2 — Cross-book timestamp mismatch (silent, signal-corrupting).** Books refresh at different times; `book_last_update` differs across books within one pull. Never compare two books' prices unless their updates are within 5 minutes (§4.1 staleness sync). An "edge" between a fresh price and a 10-minute-old price is a timestamp artifact.

**P3 — GitHub Actions cron is best-effort.** Scheduled runs are routinely delayed at peak times and are auto-disabled on repos with ~60 days of no activity. Therefore: (a) closing line is DERIVED (last snapshot before commence_time), never dependent on a punctual pre-tipoff run; (b) T5 must include a daily heartbeat job that alerts (webhook) if the day's snapshot count is zero or anomalously low.

**P4 — SQLite binary in git.** Hourly binary commits bloat history, risk merge conflicts on overlapping jobs, and every push redeploys the Streamlit Cloud app. **RESOLVED (owner decision):** append-only gzipped JSONL files in a dedicated `data` branch (one file per day per sport); SQLite is rebuilt from JSONL on read and never committed. Why: GitHub free capacity (multi-GB) far exceeds Supabase's free tier (~500MB + inactivity pausing), no external dependency, and the commit history remains the no-look-ahead audit trail. Dashboard reads the JSONL from the data branch's raw URLs, so data commits don't redeploy the app.

**P5 — Agent self-verification is invalid.** Pasted output proves nothing. Every ticket ships `scripts/verify_tN.py`; the human runs it; only its exit code counts.

**P6 — Two-outcome hardcoding.** All devig/arb/consensus math is n-outcome from day one (§4.1). Soccer (Phase 5) is 3-way.

**P7 — No shared memory between agents.** Read `DECISIONS.md` before coding; append every interface decision (names, signatures, schema, config keys) after. Team names must be normalized through one canonical mapping module the moment a second data source (scores, stats) is added.

**P8 — Anchor gaps.** Pinnacle will not quote every event. Model A emits no signal for those events (reason='no_anchor'); never silently substitute an anchor. Model B proceeds if ≥4 books quote.

**P9 — Credit exhaustion.** The API returns remaining-quota headers on every response. Log them; alert below 100 credits; degrade gracefully (drop intraday polls before dropping closing-line coverage — closing lines are the one non-negotiable dataset).


---

## 10. Current Build State (live — update as work lands)

**Working and deployed:**
- Full scaffold + core math modules; 33 tests passing, ruff clean.
- Two live providers behind the adapter interface: **The Odds API** (us+eu) and **SportsGameOdds** (MLB). Each reads its own key from `.env`; contributors stack their own free tiers.
- `src/mapping.py` canonical book/team resolution; T13 verified (4 books cross-quoted across providers).
- `provider_usage` table (our own quota tracking — SGO publishes no quota headers) and `book_deeplinks` table (SGO deep links stored per book/outcome).
- **GitHub Actions cron live**, 3×/day (16:00/20:00/00:00 UTC): poll → scan → results → data-branch commit. Data branch + manifest.json working.
- **Consensus fair-line mode active** (`fair_line_mode: consensus`). Reason: Pinnacle was found NOT to quote WNBA at pull time, and on MLB its prices were systematically 10%+ off the market with fresh timestamps (stale-but-timestamped). The market consensus is currently the more reliable fair line than the assumed sharp anchor — an empirical finding that overturned the plan's original Pinnacle-anchor assumption.
- **Median-outlier guard** in both `_consensus_signals` and `scan_arbs`: any book >5% from the median implied prob is excluded, killing phantom arbs/edges manufactured by one stale book.
- **Discord alerts live** (T12): go/no-go cards with emoji headline (bet type · team · book · price), edge, fair prob, ¼-Kelly human-rounded stake, math floor, slippage tolerance, hard expiry. Arb alerts fire only for positive-margin arbs whose books are ALL in `my_books`. Alerts gated to `my_books` and the live edge window; 30-min duplicate suppression.
- **Three-tier deep links** in alert cards: Tier 1 precise betslip (SGO, only if fresh within staleness window) → Tier 2 configured sport-page URL (`book_deeplinks_fallback`) → Tier 3 search fallback (needs team names threaded through; not yet active). Freshness is never sacrificed for a link.
- `scripts/peek.py`: self-labeling diagnostic — active signals, filter-reason breakdown, arb margins, per-event diagnostic.

**Known real-world facts discovered during build (do not re-litigate):**
- **Betfair Exchange is geo-blocked in the US** — removed from `my_books`; kept as a price source for the consensus only, never an alert target.
- **The Odds API does not provide deep links** (they don't capture book internal selection IDs). Deep links only ever come from providers that include them (SGO). This is a fixed property of each vendor, checked at selection time — not something buildable on top of a vendor that lacks it.
- SGO free tier = 2,500 objects/month, 1 object = 1 event (not per-market). ~10 events/page, paginate via `nextCursor`. Covers FanDuel, DraftKings, BetMGM, Caesars, ESPN Bet, William Hill, Bovada, Unibet, PointsBet — NOT WNBA, NOT Betfair.
- `my_books` currently: `[hardrockbet, fanatics, bovada]`. Fallback URLs verified for hardrockbet, bovada, fanatics (betfanatics.com/mlb).

**Deferred until ~1 week of data accumulates:**
- **T15 (learning loop):** `src/learn.py` — for each resolved game, devig closing prices per book, Brier-score vs winner, EWMA+shrinkage into `book_sharpness`; consensus reads learned weights once a (book, sport) has ≥30 resolved games, else the inverse-overround prior. Wired into poll workflow step (h). No manual result/weight entry ever. THIS is what will empirically resolve which books to trust and automate away the Pinnacle-outlier problem.
- **T8 (real backtest):** runs once ~100 resolved games exist.
- **T14 (Phase 6 execution engine):** exchange-API only, deterministic (no AI in order path), hard-gated on positive lag-table + 150 forward paper signals + 1 month profitable manual exchange execution.
