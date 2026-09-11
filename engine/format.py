"""Number formats the engine owns because the browser must not (§7.6 rule 1).

`significant_figures` is the second such rule, beside `_whole_units` in
`calculate.py` which §3 rule 5 fixes for the equivalence label. It exists for
`value_per_unit_display`: a conversion factor may be 2.22 or 0.0000004, and a
consumer choosing its own precision would be choosing how many digits a reader
gets to check the arithmetic with -- which is rounding, which is arithmetic.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: Enough to re-do the multiplication by hand and land on the printed result,
#: without printing the twenty places a `DECIMAL(20,10)` column can carry.
DEFAULT_FIGURES = 6


def significant_figures(value: Decimal, figures: int = DEFAULT_FIGURES) -> str:
    """`value` to `figures` significant figures: `ROUND_HALF_UP` on the
    `Decimal` (never through `float`), trailing zeros after the point removed,
    a comma every three digits of the integer part, plain notation always.

    **`ROUND_HALF_UP` is passed explicitly** for the same reason `_whole_units`
    passes it: `Decimal`'s default context rounds half to *even*, and no
    conversion factor in this tree lands on a half, so every fixture would pass
    under either mode.
    """
    if value == 0:
        return "0"
    quantum = Decimal(1).scaleb(value.adjusted() - (figures - 1))
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    digits = format(abs(rounded), "f")
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    whole, _, fraction = digits.partition(".")
    whole = f"{int(whole):,}"
    return f"{sign}{whole}.{fraction}" if fraction else f"{sign}{whole}"
