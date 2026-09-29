from decimal import Decimal

import pytest

from engine.format import significant_figures


@pytest.mark.parametrize(
    "raw, expected",
    [
        # 1/2410, the vehicle factor: six significant figures, not six places.
        ("0.00041493775933609958", "0.000414938"),
        # 1/2500000, the Olympic-pool factor: exact, and the trailing zeros go.
        ("0.0000004", "0.0000004"),
        # 1/0.45, the meal factor.
        ("2.2222222222", "2.22222"),
        # A thousands separator on the integer part, as everywhere else on the wire.
        ("1234.5678", "1,234.57"),
        ("-2.2222222222", "-2.22222"),
        ("0", "0"),
    ],
)
def test_it_renders_six_significant_figures(raw, expected):
    assert significant_figures(Decimal(raw)) == expected


def test_it_rounds_half_up_and_not_half_to_even():
    """Decimal's default context rounds half to EVEN, which would give
    '1.23456' here. No conversion factor in the tree lands on a half, so no
    fixture can tell the two modes apart -- which is why this test exists."""
    assert significant_figures(Decimal("1.2345650000")) == "1.23457"


def test_it_never_falls_back_to_scientific_notation():
    """A factor of 4e-7 formatted through str() reads '4E-7', which is not a
    number a reader checking the arithmetic by hand can use."""
    assert "E" not in significant_figures(Decimal("0.0000004"))
    assert "e" not in significant_figures(Decimal("0.0000004"))
