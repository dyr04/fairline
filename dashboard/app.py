"""Implements plan §4.8: 5-page Streamlit dashboard. Reads ONLY precomputed
artifacts + the sqlite rebuilt from JSONL (never writes, never recomputes
backtests — §7 T7/T9 /forbidden)."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from data_loader import load_db

st.set_page_config(page_title="Fair-Line Engine", layout="wide")
CFG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
conn = load_db()
page = st.sidebar.radio("Page", ["1 Live Board", "2 History", "3 Backtest",
                                 "4 Model Health", "5 Live/Paper Tracker"])


def q(sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


if page.startswith("1"):
    st.title("Live Board — fair lines & flags")
    snap = q("""SELECT event_id, home_team, away_team, commence_time, book,
                       outcome, price_decimal FROM odds_snapshots
                WHERE pulled_at = (SELECT MAX(pulled_at) FROM odds_snapshots)""")
    if snap.empty:
        st.info("No data yet — poller has not run.")
    else:
        matrix = snap.pivot_table(index=["home_team", "away_team", "outcome"],
                                  columns="book", values="price_decimal")
        st.dataframe(matrix, use_container_width=True)
    sigs = q("SELECT * FROM signals WHERE status=\"active\" ORDER BY created_at DESC LIMIT 50")
    st.subheader("Active +EV signals")
    st.dataframe(sigs, use_container_width=True)
    arbs = q("SELECT * FROM arb_events WHERE margin > 0 ORDER BY last_seen DESC LIMIT 20")
    if not arbs.empty:
        st.error(f"ARBITRAGE ACTIVE: {len(arbs)} window(s)")
        st.dataframe(arbs)

elif page.startswith("2"):
    st.title("History — line movement")
    events = q("SELECT DISTINCT event_id, home_team, away_team FROM odds_snapshots")
    if events.empty:
        st.info("No data yet.")
    else:
        label = st.selectbox("Game", events.apply(
            lambda r: f"{r.event_id} | {r.away_team} @ {r.home_team}", axis=1))
        ev = label.split(" | ")[0]
        hist = q("SELECT pulled_at, book, outcome, price_decimal FROM odds_snapshots "
                 "WHERE event_id=? ORDER BY pulled_at", (ev,))
        import plotly.express as px
        fig = px.line(hist, x="pulled_at", y="price_decimal", color="book",
                      line_dash="outcome")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(q("SELECT * FROM arb_events WHERE event_id=?", (ev,)))

elif page.startswith("3"):
    st.title("Backtest — Model A vs B vs H")
    p = Path(__file__).parent.parent / "results/metrics.json"
    if not p.exists():
        st.info("Run `python -m src.backtest` once results have accumulated (T8).")
    else:
        report = json.load(open(p))
        for model in ("model_A", "model_B", "model_H"):
            if model in report:
                st.subheader(model)
                st.json(report[model], expanded=False)
        sharp = q("SELECT * FROM book_sharpness")
        if not sharp.empty:
            st.subheader("Book sharpness (books x sports)")
            st.dataframe(sharp.pivot_table(index="book", columns="sport",
                                           values="shrunk_brier"))

elif page.startswith("4"):
    st.title("Model Health")
    p = Path(__file__).parent.parent / "results/metrics.json"
    if not p.exists():
        st.info("Model Health renders from results/metrics.json after T8.")
    else:
        report = json.load(open(p))
        st.subheader("EV buckets: promised vs realized (cap disabled, 60/40 split)")
        st.json(report.get("ev_buckets_cap_disabled", {}))
        rej = q("SELECT rejection_reason, COUNT(*) n FROM signals "
                "WHERE status=\"rejected\" GROUP BY rejection_reason")
        st.subheader("Signal rejection stats by filter")
        st.dataframe(rej)

else:
    st.title("Live / Paper Tracker")
    from src.staking import edge_at, human_round, kelly_stake
    sigs = q("SELECT * FROM signals WHERE status=\"active\" ORDER BY created_at DESC LIMIT 100")
    n_forward = len(q("SELECT id FROM live_bets"))
    st.metric("Forward-gate progress", f"{n_forward} / 150 signals")
    st.subheader("Repricer (plan §4.7)")
    if sigs.empty:
        st.info("No active signals to reprice.")
    else:
        row = sigs.iloc[st.selectbox("Signal", range(len(sigs)),
                                     format_func=lambda i: f"{sigs.iloc[i].book} "
                                     f"{sigs.iloc[i].outcome} @ {sigs.iloc[i].price_decimal}")]
        live = st.number_input("Live price your book shows now", value=float(row.price_decimal),
                               step=0.01, format="%.3f")
        e = edge_at(row.fair_prob, live)
        go = CFG["live_bet_edge_min"] <= e <= CFG["max_edge"]
        st.metric("Edge at live price", f"{e:+.2%}", delta="GO" if go else "NO-BET")
        if CFG.get("bankroll"):
            st_val = human_round(kelly_stake(row.fair_prob, live, CFG["bankroll"],
                                             CFG["kelly_fraction"]), CFG["stake_increment"])
            st.metric("Stake (human units)", f"${st_val:.0f}")
    st.subheader("Logged bets by source")
    st.dataframe(q("SELECT signal_source, COUNT(*) n, AVG(staked) avg_stake "
                   "FROM live_bets GROUP BY signal_source"))
