"""Contract §4.3. What a staff-written formula is allowed to contain."""

import pytest

from admin.expressions import ExpressionError, validate_expression

CONSTANTS = ["GWP_CH4_20", "GWP_CH4_100", "LEVY_NZD_PER_T"]


def ok(expression):
    validate_expression(expression, constant_codes=CONSTANTS)


def bad(expression):
    with pytest.raises(ExpressionError) as excinfo:
        validate_expression(expression, constant_codes=CONSTANTS)
    return excinfo.value


@pytest.mark.parametrize("expression", [
    "qty_kg",
    "qty_kg * (upstream + downstream)",
    "qty_kg * (upstream + downstream) * const_GWP_CH4",
    "qty_kg * upstream",
    "-qty_kg",
    "min(qty_kg, 100)",
    "max(0, qty_kg * downstream)",
    "abs(downstream) * qty_kg",
    "round(qty_kg * upstream, 2)",
    "qty_kg * const_LEVY_NZD_PER_T / 1000",
    "1.5 * qty_kg",
])
def test_the_contract_default_formulas_and_their_neighbours_are_accepted(expression):
    """§4.3's five defaults plus every construct the table permits."""
    ok(expression)


@pytest.mark.parametrize("expression,fragment", [
    ("qty_kg +", "syntax"),
    ("", "empty"),
    ("   ", "empty"),
])
def test_a_malformed_expression_is_refused(expression, fragment):
    assert fragment in str(bad(expression)).lower()


def test_an_unknown_variable_is_refused():
    """The commonest real mistake: a typo in a name that reads fine.

    Left unchecked this reaches the public as a 500, because the engine
    cannot bind it either.
    """
    error = bad("qty_kg * upstrem")

    assert "upstrem" in str(error)


def test_a_constant_not_in_this_factor_set_is_refused():
    """const_ names resolve against the set's own constant rows, so a formula
    referring to a constant nobody defined is broken even though it looks
    perfectly well-formed."""
    error = bad("qty_kg * const_NOT_DEFINED")

    assert "const_NOT_DEFINED" in str(error)


def test_a_constant_that_exists_is_accepted():
    ok("qty_kg * const_LEVY_NZD_PER_T")


def test_the_special_gwp_binding_is_always_available():
    """§4.3: const_GWP_CH4 resolves to the 20- or 100-year value according to
    the request, so it is valid without a constant row of that exact name."""
    ok("qty_kg * const_GWP_CH4")


@pytest.mark.parametrize("expression", [
    "__import__('os').system('rm -rf /')",
    "qty_kg.__class__",
    "qty_kg[0]",
    "[q for q in (1, 2)]",
    "lambda: 1",
    "open('/etc/passwd')",
    "eval('1')",
    "globals()",
    "qty_kg if upstream else downstream",
    "(qty_kg := 1)",
])
def test_forbidden_syntax_is_refused(expression):
    """§4.3 forbids attribute access, subscripting, function definitions,
    comprehensions, assignment, imports and every builtin name.

    The point of a whitelist rather than a blacklist is that this list does
    not have to be complete — anything not explicitly permitted is refused.
    """
    bad(expression)


def test_an_unpermitted_function_is_refused():
    """min, max, abs and round are the whole list (§4.3)."""
    error = bad("sum(qty_kg)")

    assert "sum" in str(error)


def test_an_error_carries_a_position():
    """The panel shows this beside the field, so "somewhere in this text" is
    not good enough for an expression a staff member is debugging."""
    error = bad("qty_kg * upstrem")

    assert error.line >= 1
    assert error.column >= 0
