"""Contract §4.4. The four engine exceptions and what `FormulaError` carries."""

import pytest

from engine.errors import BundleFormatError
from engine.errors import EngineError
from engine.errors import FormulaError
from engine.errors import UnknownCodeError
from engine.errors import UnknownConstantError


def test_unknown_code_error():
    assert issubclass(UnknownCodeError, EngineError)

def test_unknown_constant_error():
    assert issubclass(UnknownConstantError, EngineError)

def test_formula_error():
    assert issubclass(FormulaError, EngineError)

def test_bundle_format_error():
    """§4.4's fourth. It is the one that maps to a 400 rather than a 500, so a
    malformed inline bundle is the caller's fault and not the server's."""
    assert issubclass(BundleFormatError, EngineError)

def test_engine_error():
    assert issubclass(EngineError, Exception)


# ---------------------------------------------------------------------------
# §4.4: FormulaError's four structured attributes.
# ---------------------------------------------------------------------------


def test_a_formula_error_has_the_four_contracted_attributes():
    """§4.4 spells out `expression`, `line`, `column`, `reason` and says in
    terms that they are attributes, "not a pre-formatted message"."""
    error = FormulaError(
        "'upstrem' is not a value this formula can use.",
        expression="qty_kg * upstrem",
        line=1,
        column=9,
    )

    assert error.expression == "qty_kg * upstrem"
    assert error.line == 1
    assert error.column == 9
    assert error.reason == "'upstrem' is not a value this formula can use."


def test_the_attributes_have_defaults_so_a_bare_raise_still_type_checks():
    """Nothing in the engine raises `FormulaError()` bare any more, but
    `api/errors.py` reads all four by `getattr` at a point where it cannot
    import `engine.errors` at all — an attribute that is sometimes absent is
    worse for that caller than one that is sometimes empty."""
    error = FormulaError()

    assert (error.expression, error.line, error.column, error.reason) == ("", 1, 0, "")


def test_the_reason_is_also_the_str_so_an_untouched_caller_still_reads():
    """`str(exc)` was the only thing this error carried before, and
    `api/errors.py`'s fallback is `getattr(exc, "reason", str(exc))`. Keeping
    the two identical means no caller had to change on the day the attributes
    appeared."""
    assert str(FormulaError("Division by zero.")) == "Division by zero."


def test_the_api_builds_both_of_section_9_1s_presentations_from_them():
    """§9.1 gives this error two presentations, and **E's dry-run screen is
    the second caller waiting on these attributes** — before this task it had
    nothing to display.

    Asserted against the real `engine_problem`, not a restatement of it: the
    point is that the located presentation is *derivable* from the real
    exception, and that the public one still cannot leak the expression.
    """
    from api.errors import engine_problem

    error = FormulaError(
        "Division by zero.",
        expression="qty_kg / (upstream - upstream)",
        line=1,
        column=9,
    )

    staff = engine_problem(error, authenticated_dry_run=True)
    assert staff.status == 500
    assert staff.code == "FORMULA_ERROR"
    assert staff.details == [
        {
            "expression": "qty_kg / (upstream - upstream)",
            "line": 1,
            "column": 9,
            "reason": "Division by zero.",
        }
    ]

    public = engine_problem(error, authenticated_dry_run=False)
    assert public.details == []
    assert "qty_kg" not in public.message


@pytest.mark.parametrize("exception", [UnknownCodeError, UnknownConstantError,
                                       BundleFormatError])
def test_the_other_three_still_take_a_plain_message(exception):
    """A constructor change on one exception must not have quietly changed the
    others' — they are raised with a positional string all over
    `engine/calculate.py` and `engine/bundle.py`."""
    assert str(exception("unknown sector: 'nope'")) == "unknown sector: 'nope'"
