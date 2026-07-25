"""Plan §7 T4 step 5: arb cases, stake split, staleness, one case per rejection path."""
import pytest

from src.signal_engine import compute_signals, scan_arbs, stake_split
from tests.synth import five_book_event, market, row

CFG = {"anchor_book": "pinnacle", "anchor_overround_band": [0.015, 0.08],
       "soft_overround_band": [0.015, 0.15], "min_books": 4,
       "staleness_sync_minutes": 5, "edge_threshold": 0.02, "max_edge": 0.08,
       "exchange_books": {}}


def actives(rows, cfg=CFG):
    return [s for s in compute_signals(rows, cfg) if s.status == "active"]


def rejected(rows, reason, cfg=CFG):
    return [s for s in compute_signals(rows, cfg) if s.rejection_reason == reason]


def test_positive_ev_signal_fires():
    sigs = actives(five_book_event())
    assert any(s.book == "caesars" and s.outcome == "A" for s in sigs)
    s = next(s for s in sigs if s.book == "caesars" and s.outcome == "A")
    assert 0.02 <= s.edge <= 0.08
    assert s.fair_prob == pytest.approx(0.5, abs=1e-9)  # 1.95/1.95 devigs to .5/.5


def test_no_anchor_rejects_event():
    rows = five_book_event()
    rows = [r for r in rows if r["book"] != "pinnacle"]
    assert len(rejected(rows, "no_anchor")) > 0
    assert actives(rows) == []


def test_min_books_rejection():
    rows = market("pinnacle", 1.95, 1.95, batch="p1") + market("caesars", 2.30, 1.70, batch="c1")
    assert len(rejected(rows, "min_books")) > 0


def test_stale_sync_rejection():
    rows = five_book_event()
    rows += market("stalebook", 2.40, 1.65, batch="s1", upd_min=-20)  # 20 min stale
    assert len(rejected(rows, "stale_sync")) == 2


def test_max_edge_rejection():
    # caesars 2.55/1.55: overround 3.7% (in band); devigged A=.378 -> edge .122 > 8% cap
    rows = five_book_event()
    rows = [r for r in rows if r["book"] != "caesars"]
    rows += market("caesars", 2.55, 1.55, batch="c1")
    assert len(rejected(rows, "max_edge")) >= 1


def test_overround_rejection_wide_soft_book():
    rows = five_book_event()
    rows += market("juicybook", 1.55, 1.55, batch="j1")  # overround 29% > 15% band
    assert len(rejected(rows, "overround")) == 2


def test_below_threshold_rejection():
    rows = five_book_event(soft_price_a=2.03)
    assert len(rejected(rows, "below_threshold")) >= 1


def test_arb_known_margin():
    # symmetric 2.03874: margin = 1 - 2/2.03874 = 1.9%
    rows = market("bookx", 2.03874, 1.50, batch="x1") + market("booky", 1.50, 2.03874, batch="y1")
    arbs = scan_arbs(rows, CFG)
    assert len(arbs) == 1
    assert arbs[0]["margin"] == pytest.approx(0.019, abs=1e-4)


def test_arb_negative_case():
    rows = market("bookx", 1.90, 1.90, batch="x1") + market("booky", 1.88, 1.92, batch="y1")
    assert scan_arbs(rows, CFG) == []  # margin about -5%, below near-arb floor


def test_stake_split_equal_payoff():
    odds = [2.10, 2.10]
    st = stake_split(100.0, odds)
    assert sum(st) == pytest.approx(100.0)
    payoffs = [s * o for s, o in zip(st, odds)]
    assert payoffs[0] == pytest.approx(payoffs[1])


def test_incomplete_group_skipped():
    rows = five_book_event()
    rows.append(row("onesided", "A", 2.50, batch="o1"))  # only one outcome
    assert all(s.book != "onesided" for s in compute_signals(rows, CFG))
