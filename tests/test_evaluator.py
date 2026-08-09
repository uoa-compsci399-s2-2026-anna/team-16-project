"""Contract §4.3 and §4.4. What the engine will actually run.

`admin/expressions.py` is this module's static twin: the panel validates a
formula with that, and the engine then runs it with this. **Any expression one
accepts and the other refuses is a defect by construction** — the panel calls a
formula valid, a staff member saves it, and the next public calculation returns
FORMULA_ERROR 500. `TestThePanelAndTheEngineAgree` at the bottom of this file is
the test that keeps the two from drifting apart again; the tests above it pin
the individual rules.
"""

from decimal import Decimal

import pytest

from admin.expressions import ExpressionError, validate_expression
from engine.errors import FormulaError
from engine.evaluator import evaluate

# ---------------------------------------------------------------------------
# A's original seven. Extended, not replaced.
# ---------------------------------------------------------------------------


def test_evaluate_basic_formula():
    result = evaluate(
        "qty_kg * (upstream + downstream)",
        {
            "qty_kg": Decimal("100"),
            "upstream": Decimal("1.9"),
            "downstream": Decimal("0.99"),
        }
    )

    assert result == Decimal("289")

def test_evaluate_invalid_formula():
    with pytest.raises(FormulaError):
        evaluate(
            "qty_kg + unknown",
            {
                "qty_kg": Decimal("100")
            }
        )

def test_evaluate_reject_function_call():
    with pytest.raises(FormulaError):
        evaluate(
            "__import__('os')",
            {}
        )

def test_evaluate_safe_function():

    result = evaluate(
        "max(upstream, downstream)",
        {
            "upstream": Decimal("1.9"),
            "downstream": Decimal("0.99"),
        }
    )

    assert result == Decimal("1.9")

def test_evaluate_reject_unsafe_function():

    with pytest.raises(FormulaError):
        evaluate(
            "open('test.txt')",
            {}
        )

def test_evaluate_negative_number():
    result = evaluate(
        "-5",
        {}
    )

    assert result == Decimal("-5")

def test_evaluate_negative_variable():
    result = evaluate(
        "-upstream",
        {
            "upstream": Decimal("1.9")
        }
    )

    assert result == Decimal("-1.9")


# ---------------------------------------------------------------------------
# The adversarial set, extended from A's two.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expression", [
    "__import__('os')",
    "__import__('os').system('rm -rf /')",
    "open('test.txt')",
    "eval('1')",
    "exec('x=1')",
    "globals()",
    "vars()",
    "qty_kg.__class__",
    "(1).__class__",
    "qty_kg.__class__.__mro__[1]",
    "qty_kg.real",
    "qty_kg[0]",
    "[q for q in (1, 2)]",
    "lambda: 1",
    "(qty_kg := 1)",
])
def test_nothing_a_formula_can_write_reaches_outside_the_expression(expression):
    """A formula is text a browser can put in front of this evaluator through
    `dry_run.bundle` (§6.2.1), which the panel never validated. None of these
    may produce a value, and — since the fix below — none may quietly produce
    `None` either.

    The whitelist is what makes this list not need to be complete: a construct
    nobody anticipated is refused because it was never admitted.
    """
    with pytest.raises(FormulaError):
        evaluate(expression, {"qty_kg": Decimal("100")})


# ---------------------------------------------------------------------------
# 1. An unhandled node type raises. It must not return None.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expression", [
    "1 < 2",
    "1 and 2",
    "qty_kg if upstream else downstream",
    "[1, 2]",
    "(1, 2)",
    "{1: 2}",
    "{1, 2}",
    "qty_kg.real",
    "f'{qty_kg}'",
])
def test_an_unhandled_node_type_raises_rather_than_returning_none(expression):
    """The soundness hole, and it is not an escape — it is silence.

    `_evaluate_node` had no else: a node type it did not recognise fell off
    the end and returned `None`. `1 < 2` did not raise, it evaluated to
    `None`, which becomes `MetricResult.total`, which reaches the wire as
    `"total": null` or blows up several frames later in the roll-up with a
    `TypeError` naming a line that is nowhere near the formula.

    Asserting `FormulaError` rather than merely "not None" is deliberate: a
    fix that returned `Decimal("0")` for an unknown node would pass a
    not-None assertion and be worse than the bug.
    """
    with pytest.raises(FormulaError):
        evaluate(expression, {"qty_kg": Decimal("100"), "upstream": Decimal("1"),
                              "downstream": Decimal("1")})


def test_an_unhandled_node_names_what_it_refused():
    """§4.4's `reason` has to be actionable. "A comparison is not allowed in a
    formula" tells a staff member what to delete; the empty string does not."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("qty_kg < 2", {"qty_kg": Decimal("100")})

    assert "comparison" in excinfo.value.reason.lower()


@pytest.mark.parametrize("expression", ["qty_kg ** 2", "qty_kg % 2", "qty_kg // 2"])
def test_an_operator_outside_the_whitelist_raises_with_its_own_reason(expression):
    """§4.3 permits + - * / and unary minus. These reached `FormulaError` only
    by way of a `KeyError` caught by the blanket handler, so the reason a
    staff member saw was the string `<class 'ast.Pow'>`."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate(expression, {"qty_kg": Decimal("100")})

    assert "ast." not in excinfo.value.reason


# ---------------------------------------------------------------------------
# 2. round()'s ndigits. The live defect.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expression,expected", [
    ("round(qty_kg, 2)", Decimal("100.46")),
    ("round(qty_kg, 0)", Decimal("100")),
    ("round(qty_kg, -1)", Decimal("1.0E+2")),
    ("round(qty_kg, -2)", Decimal("1E+2")),
    ("round(qty_kg)", Decimal("100")),
])
def test_round_with_a_digit_count_runs(expression, expected):
    """**The live defect.** `admin/expressions.py::_check_call` explicitly
    permits `round` with one or two arguments and `_is_int_literal` exists
    solely to validate the second — so the panel saves `round(qty_kg, 2)`
    happily. The engine coerced every argument to `Decimal` before the call,
    ran `round(Decimal('100'), Decimal('2'))`, and `Decimal` is not an `int`.

    A staff member saved a formula the panel called valid and the next public
    calculation returned FORMULA_ERROR 500.

    `-1` is here because the panel's `_is_int_literal` reasons carefully about
    it (`-1` parses as `UnaryOp(USub, Constant(1))`, not a negative literal)
    for a call the engine could not make at all.
    """
    assert evaluate(expression, {"qty_kg": Decimal("100.456")}) == expected


def test_round_returns_a_decimal_not_an_int():
    """One-argument `round(Decimal)` returns a Python `int`. Every value the
    evaluator hands back is summed into a `Decimal` total, so the conversion
    happens here rather than at the call site."""
    assert isinstance(evaluate("round(qty_kg)", {"qty_kg": Decimal("100.4")}), Decimal)


@pytest.mark.parametrize("expression", [
    "round(qty_kg, qty_kg)",
    "round(qty_kg, 2.0)",
    "round(qty_kg, True)",
    "round(qty_kg, -qty_kg)",
    "round(qty_kg, 1 + 1)",
])
def test_rounds_digit_count_must_still_be_a_whole_number_literal(expression):
    """The fix must not overcorrect into "coerce whatever is there to int".

    The panel accepts only a bare integer literal, optionally negated, and
    refuses all five of these. If the engine accepted any of them the two
    would diverge again — in the harmless direction today, but the harmless
    direction is how the panel stops being the thing that defines the
    language.
    """
    with pytest.raises(FormulaError):
        evaluate(expression, {"qty_kg": Decimal("100.456")})


# ---------------------------------------------------------------------------
# 3. Non-finite constants.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expression", ["1e999", "qty_kg * 1e999", "-1e999"])
def test_a_non_finite_literal_is_refused(expression):
    """`1e999` parses to `float('inf')`, and `Decimal('Infinity')` is a
    perfectly valid `Decimal` — so it propagated silently through every
    subsequent operation and only failed at serialisation, after the number
    had been computed.

    `admin/expressions.py::_check_constant` predicted this exact failure and
    guarded it. The engine is the half that was predicted about.
    """
    with pytest.raises(FormulaError):
        evaluate(expression, {"qty_kg": Decimal("100")})


def test_a_result_that_is_not_finite_is_refused():
    """§4.3 lists "non-finite result" as a raise trigger in its own right.
    With non-finite literals refused and division by zero raising, no path
    reaches this today — which is the reason to pin it, because the next
    function added to the whitelist could open one."""
    with pytest.raises(FormulaError):
        evaluate("qty_kg", {"qty_kg": Decimal("Infinity")})


@pytest.mark.parametrize("expression", ["1/0", "qty_kg / (upstream - upstream)"])
def test_division_by_zero_is_refused(expression):
    with pytest.raises(FormulaError) as excinfo:
        evaluate(expression, {"qty_kg": Decimal("100"), "upstream": Decimal("1.9")})

    assert "zero" in excinfo.value.reason.lower()


def test_an_oversized_integer_literal_is_exact_not_refused():
    """A 400-digit integer is a decimal number, which is what §4.3 permits,
    and `Decimal` represents it exactly. The panel accepts it (and takes care
    not to crash deciding, because `math.isfinite` raises `OverflowError` on
    an int this large); the finiteness check here must not refuse it."""
    assert evaluate("9" * 400, {}) == Decimal("9" * 400)


# ---------------------------------------------------------------------------
# 4. §4.4's four attributes.
# ---------------------------------------------------------------------------


def test_a_formula_error_carries_the_four_contracted_attributes():
    """§4.4. E's dry-run screen renders all four beside the field, and
    `api/errors.py::engine_problem` reads all four by name."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("qty_kg * upstrem", {"qty_kg": Decimal("100")})
    error = excinfo.value

    assert error.expression == "qty_kg * upstrem"
    assert error.line == 1
    assert error.column == 9
    assert "upstrem" in error.reason


def test_the_column_matches_the_panels_zero_based_convention():
    """`admin/expressions.py` reports column 9 for the same typo (its own
    `test_an_error_carries_a_position`). Two located errors for one formula
    that point at different characters are worse than one unlocated one."""
    with pytest.raises(ExpressionError) as panel:
        validate_expression("qty_kg * upstrem", constant_codes=[])
    with pytest.raises(FormulaError) as engine:
        evaluate("qty_kg * upstrem", {"qty_kg": Decimal("100")})

    assert (engine.value.line, engine.value.column) == (panel.value.line, panel.value.column)


def test_a_syntax_error_column_is_zero_based_too():
    """`SyntaxError.offset` is 1-based; the panel converts it and so must
    this, or the caret in the dry-run view points one character right."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("1 +* 2", {})

    assert excinfo.value.column == 3


def test_the_position_is_the_offending_nodes_own_not_the_expressions():
    """Threading line and column from the AST is the whole point: a formula
    long enough to be worth debugging has one bad token in it somewhere, and
    `column=0` for every error is the same as no column at all."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("qty_kg + qty_kg + [1]", {"qty_kg": Decimal("1")})

    assert excinfo.value.column == 18


def test_the_expression_is_stamped_on_an_error_from_deep_in_the_tree():
    """`expression` is what §9.1 shows staff and never shows the public. An
    inner node knows its position but not the text it came from, so the stamp
    happens on the way out of `evaluate` — including on the syntax-error path,
    which never builds a tree at all."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("qty_kg +", {"qty_kg": Decimal("1")})

    assert excinfo.value.expression == "qty_kg +"


def test_an_empty_formula_says_so():
    with pytest.raises(FormulaError) as excinfo:
        evaluate("   ", {})

    assert "empty" in excinfo.value.reason.lower()


def test_the_reason_never_leaks_a_python_repr():
    """`FormulaError(str(e))` on a `decimal` exception produced the reason
    `[<class 'decimal.ConversionSyntax'>]`, which is not a sentence and is
    shown to a staff member."""
    for expression in ("qty_kg * True", "qty_kg + None", '"os" * qty_kg'):
        with pytest.raises(FormulaError) as excinfo:
            evaluate(expression, {"qty_kg": Decimal("1")})
        assert "<class" not in excinfo.value.reason


def test_the_public_presentation_never_needs_the_expression():
    """§9.1: the expression is never echoed on a public request. This asserts
    the split is *possible* — that `reason` stands alone and the API is not
    forced to print `str(exc)` to say anything at all."""
    with pytest.raises(FormulaError) as excinfo:
        evaluate("qty_kg * upstrem", {"qty_kg": Decimal("100")})

    assert "qty_kg * upstrem" not in excinfo.value.reason


# ---------------------------------------------------------------------------
# 5. The test that stops the divergence reopening.
# ---------------------------------------------------------------------------

#: Every expression here is one whose verdict is a property of the *language*,
#: not of the data — so the panel's static answer and the engine's runtime
#: answer are comparable. Runtime-only failures (division by zero, an unbound
#: variable name the panel would have resolved against a different factor set)
#: are excluded deliberately and covered separately below.
AGREEMENT_CASES = [
    # §4.3's five defaults and their neighbours.
    "qty_kg",
    "qty_kg * (upstream + downstream)",
    "qty_kg * (upstream + downstream) * const_GWP_CH4",
    "qty_kg * upstream",
    "qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)",
    "-qty_kg",
    "1.5 * qty_kg",
    "qty_kg - downstream",
    "qty_kg * const_LEVY_NZD_PER_T / 1000",
    "9" * 400,
    # Calls.
    "min(qty_kg, 100)",
    "max(0, qty_kg * downstream)",
    "min(qty_kg, 100, downstream)",
    "abs(downstream) * qty_kg",
    "abs(qty_kg * upstream)",
    "round(qty_kg)",
    "round(qty_kg, 2)",
    "round(qty_kg * upstream, 2)",
    "round(qty_kg * upstream, -1)",
    # Arity and call shape.
    "min()", "max()", "min(qty_kg)", "max(qty_kg)",
    "abs()", "abs(qty_kg, upstream)",
    "round()", "round(qty_kg, 2, 3)",
    "round(qty_kg, qty_kg)", "round(qty_kg, 2.0)", "round(qty_kg, True)",
    "round(qty_kg * upstream, -qty_kg)",
    "round(qty_kg, ndigits=2)",
    "sum(qty_kg)", "len(qty_kg)", "print(qty_kg)",
    # Operators.
    "qty_kg ** 2", "qty_kg % 2", "qty_kg // 2",
    "+qty_kg", "qty_kg * +upstream", "not qty_kg", "~qty_kg",
    "1 < 2", "1 and 2", "1 or 2",
    # Literals.
    '"os" * qty_kg', "qty_kg * True", "qty_kg + None", "qty_kg * b'x'",
    "qty_kg * 1j", "qty_kg * ...", "qty_kg * 1e999", "1e999", "-1e999",
    # Forbidden syntax.
    "__import__('os')", "__import__('os').system('rm -rf /')",
    "open('/etc/passwd')", "eval('1')", "globals()",
    "qty_kg.__class__", "(1).__class__", "qty_kg.real", "qty_kg[0]",
    "[q for q in (1, 2)]", "(1, 2)", "[1, 2]", "{1: 2}", "{1, 2}",
    "lambda: 1", "(qty_kg := 1)", "qty_kg if upstream else downstream",
    "f'{qty_kg}'",
    # Malformed.
    "qty_kg +", "", "   ", "1 +* 2",
    # Names.
    "qty_kg * upstrem", "qty_kg * const_NOT_DEFINED",
]

#: The constants the panel resolves `const_<CODE>` against, and the same set
#: bound as `const_<CODE>` variables for the engine. `const_GWP_CH4` is §4.3's
#: special binding and is available to both without a constant row of that name.
AGREEMENT_CONSTANTS = ["GWP_CH4_20", "GWP_CH4_100", "LEVY_NZD_PER_T",
                       "FOOD_VALUE_PER_KG"]

AGREEMENT_VARIABLES = {
    "qty_kg": Decimal("100.456"),
    "upstream": Decimal("1.9"),
    "downstream": Decimal("0.99"),
    "const_GWP_CH4": Decimal("28"),
    **{f"const_{code}": Decimal("1") for code in AGREEMENT_CONSTANTS},
}


def _panel_accepts(expression):
    try:
        validate_expression(expression, constant_codes=AGREEMENT_CONSTANTS)
    except ExpressionError:
        return False
    return True


def _engine_accepts(expression):
    try:
        evaluate(expression, AGREEMENT_VARIABLES)
    except FormulaError:
        return False
    return True


@pytest.mark.parametrize("expression", AGREEMENT_CASES)
def test_the_panel_and_the_engine_reach_the_same_verdict(expression):
    """**The test that stops this reopening.**

    Staff write formulas through the admin panel, which validates them with
    `admin/expressions.py`; the engine then runs them. Neither module can
    change its answer without this failing.

    The two failure directions are not symmetric and both matter:

    - *panel accepts, engine refuses* is a live 500. A staff member saves a
      formula the panel called valid and the next member of the public to run
      a calculation gets FORMULA_ERROR. `round(qty_kg, 2)` was exactly this.
    - *panel refuses, engine accepts* is not a 500, but it means the panel has
      stopped being the definition of the language — the engine will run
      something no reviewer of `admin/expressions.py` believes is legal, and
      `dry_run.bundle` (§6.2.1) is a path that reaches the engine without
      passing the panel at all. `+qty_kg`, `round(qty_kg, ndigits=2)` and
      `1e999` were all this.

    Whichever direction it fires in, the module to change is usually the
    engine: the panel validates arity, refuses non-finite literals, names the
    node type it refused and carries line and column.
    """
    panel, engine = _panel_accepts(expression), _engine_accepts(expression)

    assert panel == engine, (
        f"{expression!r}: the panel {'accepts' if panel else 'refuses'} it and "
        f"the engine {'accepts' if engine else 'refuses'} it. "
        f"{'A staff member can save this and the next public calculation 500s.' if panel else 'The engine runs something the panel calls illegal.'}"
    )


def test_the_agreement_set_is_not_all_one_verdict():
    """A guard on the guard. `_panel_accepts` and `_engine_accepts` returning
    a constant would satisfy every case above; this fails if the corpus ever
    stops exercising both answers."""
    verdicts = {_panel_accepts(expression) for expression in AGREEMENT_CASES}

    assert verdicts == {True, False}


def test_round_with_a_digit_count_is_the_case_that_broke():
    """Named separately from the parametrised sweep so that the regression has
    a test whose name says what it is, and so it cannot be dropped from the
    corpus without notice."""
    assert _panel_accepts("round(qty_kg, 2)")
    assert _engine_accepts("round(qty_kg, 2)")


@pytest.mark.parametrize("expression", ["1/0", "qty_kg / (upstream - upstream)"])
def test_the_one_intended_asymmetry_is_data_dependent_failure(expression):
    """The panel is static and the engine is dynamic, so they cannot agree on
    everything and should not try.

    `admin/expressions.py`'s docstring draws the line itself: "Whether an
    expression divides by zero on real data is what a dry run is for." A
    formula that always divides by zero is savable and fails when run — which
    is why §8.2's dry run exists and why these cases are held out of
    `AGREEMENT_CASES` rather than quietly passing because the corpus omits
    them.
    """
    assert _panel_accepts(expression)
    assert not _engine_accepts(expression)


def test_an_unbound_name_is_the_other_half_of_that_line():
    """The panel resolves `const_X` against one factor set's constant rows;
    the engine resolves it against the bundle it was handed. The same
    expression is legal against one factor set and not another, so a name
    the panel accepts can still be unbound at run time — a *configuration*
    disagreement, not a language one."""
    assert _panel_accepts("qty_kg * const_LEVY_NZD_PER_T")
    with pytest.raises(FormulaError):
        evaluate("qty_kg * const_LEVY_NZD_PER_T", {"qty_kg": Decimal("1")})
