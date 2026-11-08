import pytest

from app.discount import apply_discount


def test_hidden_fractional_discount():
    assert apply_discount(200, 12.5) == 175


def test_hidden_negative_fraction_raises():
    with pytest.raises(ValueError):
        apply_discount(200, -0.5)


def test_hidden_over_100_fraction_raises():
    with pytest.raises(ValueError):
        apply_discount(200, 100.5)
