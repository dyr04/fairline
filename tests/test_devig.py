"""The six tests named in plan §7 T3 step 5."""
import pytest

from src.devig import devig_multiplicative, devig_power, overround


def test_two_outcome_sums_to_one():
    for fn in (devig_multiplicative, devig_power):
        assert sum(fn([2.10, 1.80])) == pytest.approx(1.0)


def test_three_outcome_sums_to_one():
    for fn in (devig_multiplicative, devig_power):
        assert sum(fn([2.50, 3.30, 3.10])) == pytest.approx(1.0)  # soccer 1X2


def test_hand_computed_multiplicative():
    p = devig_multiplicative([2.10, 1.80])
    assert p[0] == pytest.approx(0.461538, abs=1e-6)
    assert p[1] == pytest.approx(0.538462, abs=1e-6)


def test_power_matches_multiplicative_at_tiny_vig():
    odds = [2.001, 2.001]
    assert overround(odds) < 0.005
    pm, pp = devig_multiplicative(odds), devig_power(odds)
    assert pm[0] == pytest.approx(pp[0], abs=1e-4)


def test_power_favors_favorite_on_longshot_market():
    odds = [1.10, 9.00]
    pm, pp = devig_multiplicative(odds), devig_power(odds)
    assert pp[0] > pm[0]


def test_invalid_odds_raise():
    with pytest.raises(ValueError):
        devig_multiplicative([1.0, 2.0])
    with pytest.raises(ValueError):
        devig_power([2.0])
