import pytest

from src.staking import edge_at, human_round, kelly_stake, price_floor


def test_kelly_known_case():
    assert kelly_stake(0.55, 2.00, 1000, 0.25) == pytest.approx(25.0)


def test_kelly_negative_edge_floors_at_zero():
    assert kelly_stake(0.45, 2.00, 1000) == 0.0


def test_human_round_9834_case():
    assert human_round(98.34, 5) == 95.0


def test_human_round_never_exceeds_stake():
    for stake in (7.2, 99.99, 103.0, 4.9):
        assert human_round(stake, 5) <= stake


def test_price_floor_roundtrip():
    fair, min_edge = 0.55, 0.035
    floor = price_floor(fair, min_edge)
    assert edge_at(fair, floor) == pytest.approx(min_edge)
