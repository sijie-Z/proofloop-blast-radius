import pytest

from app.discount import apply_discount


def test_normal_discount():
    assert apply_discount(100, 10) == 90


def test_zero_discount():
    assert apply_discount(100, 0) == 100


def test_full_discount():
    assert apply_discount(100, 100) == 0


def test_negative_percent_raises():
    with pytest.raises(ValueError):
        apply_discount(100, -5)


def test_over_100_percent_raises():
    with pytest.raises(ValueError):
        apply_discount(100, 150)
