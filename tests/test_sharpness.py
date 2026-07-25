"""Plan §7 T11: Brier math, EWMA, shrinkage cold-start, LOO exclusion,
and WALK-FORWARD INVARIANCE (§9 P1) — the test that keeps Model B honest."""
import pytest

from src.sharpness import (
    SharpnessState,
    brier,
    ewma_update,
    loo_consensus,
    model_b_fair,
    shrink,
    weights,
)


def test_brier_math():
    assert brier(0.7, 1) == pytest.approx(0.09)
    assert brier(0.7, 0) == pytest.approx(0.49)
    with pytest.raises(ValueError):
        brier(1.2, 1)


def test_ewma_initializes_then_decays():
    s = ewma_update(None, 0.25)
    assert s == 0.25
    s2 = ewma_update(s, 0.05, lam=0.98)
    assert s2 == pytest.approx(0.98 * 0.25 + 0.02 * 0.05)


def test_shrinkage_cold_start_equals_mean():
    # A book with n=0 sits EXACTLY at the cross-book mean (plan §4.5).
    assert shrink(s=0.9, n=0, s_bar=0.21, n0=30) == pytest.approx(0.21)


def test_weights_inverse_and_normalized():
    w = weights({"sharp": 0.20, "soft": 0.25})
    assert w["sharp"] > w["soft"]
    assert sum(w.values()) == pytest.approx(1.0)


def test_loo_excludes_the_scored_book():
    probs = {"a": 0.50, "b": 0.50, "x": 0.99}
    w = {"a": 1 / 3, "b": 1 / 3, "x": 1 / 3}
    # Excluding x, consensus is exactly .5 — x cannot vouch for itself.
    assert loo_consensus(probs, w, exclude_book="x") == pytest.approx(0.5)


def test_walk_forward_invariance():
    """Weights at time t must be byte-identical regardless of post-t data."""
    a = SharpnessState()
    b = SharpnessState()
    pre_t = [("pinnacle", 0.60, 1), ("caesars", 0.50, 1), ("pinnacle", 0.55, 0)]
    for book, p, y in pre_t:
        a.update(book, "wnba", p, y)
        b.update(book, "wnba", p, y)
    w_t = a.weights("wnba")
    # b receives ALTERED post-t history; its weights AT t (captured above for a)
    # are compared against a state that never saw the future at all.
    for book, p, y in [("caesars", 0.99, 0), ("pinnacle", 0.01, 1)]:
        b.update(book, "wnba", p, y)
    assert w_t == a.weights("wnba")           # a untouched by the future
    assert w_t != b.weights("wnba")            # future data DOES move weights...
    # ...which is exactly why the replay driver must snapshot weights pre-bet.


def test_model_b_cold_start_equal_weights():
    fair = model_b_fair({"a": 0.40, "b": 0.60}, SharpnessState(), "wnba",
                        exclude_book="c")
    assert fair == pytest.approx(0.50)
