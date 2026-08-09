"""Restricted evaluation of a staff-written formula. Contract §4.3.

A formula is text a staff member types into the admin panel and the engine
then executes on every calculation, so this function is pointed at input that
originated in a browser — through `dry_run.bundle` (§6.2.1) it is pointed at
input the panel never validated at all.

**Whitelist, not blacklist.** `OPERATORS` and `SAFE_FUNCTIONS` are dicts you
add to, and `_evaluate_node` refuses any node type it does not name. A
construct nobody anticipated is refused by default rather than admitted by
default; that is the property that makes the language's security boundary
argument hold without having to enumerate what is dangerous.

**`admin/expressions.py` is this module's static twin.** It validates the same
text at save time; this runs it. Any expression one accepts and the other
refuses is a defect by construction — a formula the panel calls valid, saved,
and then returning FORMULA_ERROR 500 to a member of the public. The two are
deliberately independent implementations (one walks the tree checking node
types, one walks it computing), and `tests/test_evaluator.py`'s
`test_the_panel_and_the_engine_reach_the_same_verdict` is what keeps them from
drifting. **Any change here needs the same change there, or that test fails.**

The one asymmetry, drawn on purpose: the panel is static and this is dynamic,
so `1/0` is savable and fails when run. That is what a dry run is for.

**A formula computes one line; the engine performs the summation** (§4.3), so
the language needs no arrays, no loops and no `sum()` — which is why refusing
every container node below costs nothing.
"""

import ast
import decimal
from decimal import Decimal

from engine.errors import FormulaError

#: §4.3: `+ - * / ( )`. `**` and `%` are absent deliberately — the contract
#: does not list them, and `**` with a large exponent is the one arithmetic
#: operator that can exhaust memory before any timeout notices.
OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}

#: §4.3: the entire list. Mirrors `admin.expressions.PERMITTED_FUNCTIONS`.
SAFE_FUNCTIONS = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
}

#: `(minimum, maximum)` arguments, `None` for no maximum. Mirrors the arity
#: half of `admin.expressions._check_call`, and is checked here rather than
#: left to CPython's own `TypeError` so that the verdict is a property of this
#: whitelist rather than of a builtin's signature. `min`/`max` need two
#: because Python's one-argument form means "iterable" and no node this module
#: permits can produce one.
ARITY = {
    "min": (2, None),
    "max": (2, None),
    "abs": (1, 1),
    "round": (1, 2),
}

#: §4.3 permits unary minus and does not mention unary plus, so `ast.UAdd` is
#: absent here for the same reason `ast.Pow` is absent from `OPERATORS`. The
#: panel refuses `+qty_kg` and has a test saying why; an extra whitelist entry
#: nobody asked for is precisely the drift this design exists to prevent.
UNARY_OPERATORS = {
    ast.USub: lambda a: -a,
}

#: Readable names for what was refused, so §4.4's `reason` is a sentence a
#: staff member can act on rather than `<class 'ast.Compare'>`. Kept in step
#: with `admin.expressions._DESCRIPTIONS`; the entries below it lacks are node
#: types the panel refuses through its `_PERMITTED_NODES` tuple instead.
_DESCRIPTIONS = {
    ast.Attribute: "Attribute access", ast.Subscript: "Subscripting",
    ast.Lambda: "A function definition", ast.ListComp: "A comprehension",
    ast.SetComp: "A comprehension", ast.DictComp: "A comprehension",
    ast.GeneratorExp: "A comprehension", ast.IfExp: "A conditional",
    ast.NamedExpr: "Assignment", ast.Compare: "A comparison",
    ast.BoolOp: "A boolean operator", ast.List: "A list",
    ast.Tuple: "A tuple", ast.Dict: "A dictionary", ast.Set: "A set",
    ast.JoinedStr: "An f-string", ast.FormattedValue: "An f-string",
    ast.Starred: "Argument unpacking", ast.Await: "An await expression",
    ast.Slice: "A slice", ast.Yield: "A yield expression",
}

_OPERATOR_DESCRIPTIONS = {
    ast.Pow: "**", ast.Mod: "%", ast.FloorDiv: "//",
    ast.MatMult: "@", ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
    ast.LShift: "<<", ast.RShift: ">>",
    ast.UAdd: "unary +", ast.Not: "not", ast.Invert: "~",
}


def _fail(node: ast.AST, reason: str) -> None:
    """Raise with the offending node's own position.

    `column=0` for every error is the same as no column at all, and §9.1's
    staff presentation exists so that someone tuning a long formula can see
    which token is wrong.
    """
    raise FormulaError(
        reason,
        line=getattr(node, "lineno", 1),
        column=getattr(node, "col_offset", 0),
    )


def _describe(node: ast.AST) -> str:
    return _DESCRIPTIONS.get(type(node), type(node).__name__)


def _int_literal(node: ast.AST) -> int | None:
    """`round`'s `ndigits` as an `int`, or `None` if this node is not one.

    **This is the live defect the whole task turned on.** Every argument used
    to be converted to `Decimal` before the call, so `round(qty_kg, 2)` ran
    `round(Decimal('100'), Decimal('2'))` and `Decimal` is not an `int` —
    while `admin/expressions.py` accepted the same text and let a staff member
    save it.

    The shape accepted is exactly `admin.expressions._is_int_literal`'s: a
    bare non-boolean integer literal, optionally negated once. Negative
    `ndigits` (round to tens, hundreds) is a legitimate request and Python has
    no negative-literal syntax — `-1` parses as `UnaryOp(USub, Constant(1))` —
    so one level of unary minus is unwrapped. Only one: after unwrapping,
    `-qty_kg`'s operand is a `Name`, which is still refused. The rule is "a
    literal, optionally negated", not "anything built from unary minus".

    Deliberately a *static* check on the node rather than a coercion of the
    evaluated value. Coercing would make the engine accept `round(x, qty_kg)`,
    which the panel refuses — harmless today, but it is how the panel stops
    being the definition of the language.
    """
    negate = False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node, negate = node.operand, True
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return -node.value if negate else node.value
    return None


def _constant(node: ast.Constant) -> Decimal:
    """§4.3 permits decimal-number literals and nothing else.

    `ast.Constant` covers every Python literal — strings, bytes, `None`,
    complex, `Ellipsis` and `bool` as well as `int`/`float`. `bool` is an
    `int` subclass and must be excluded by name.

    The type check and the finiteness check are separate for the reason
    `admin/expressions.py` gives: an oversized integer literal is not an error
    at all (`Decimal` represents it exactly), and only a `float` can be
    non-finite. `1e999` parses to `float('inf')`, and `Decimal('Infinity')` is
    a **valid** `Decimal` — so before this check it propagated silently
    through every subsequent operation and failed only at serialisation, long
    after the number had been computed. The panel predicted that exact failure
    and guarded it; this is the half that was predicted about.
    """
    value = node.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(node, f"{type(value).__name__} is not a decimal number a formula can use.")
    if isinstance(value, float):
        # `Decimal(str(float))` recovers the shortest round-tripping
        # representation, which is exact for any literal a staff member will
        # realistically type. An int goes to `Decimal` directly — no float,
        # no string, exact at any size.
        if value != value or value in (float("inf"), float("-inf")):
            _fail(node, f"{value!r} is not a finite number a formula can use.")
        return Decimal(str(value))
    return Decimal(value)


def _call(node: ast.Call, variables: dict[str, Decimal]) -> Decimal:
    """One of the four permitted functions, with an argument list the engine
    can actually run.

    Checking the name alone lets through calls that pass a name check and blow
    up at run time — `abs(qty_kg, upstream)`, `round(qty_kg, 2, 3)` — as a
    `TypeError` the public sees as FORMULA_ERROR 500.
    """
    if not isinstance(node.func, ast.Name):
        _fail(node, "Only min, max, abs and round may be called in a formula.")
    name = node.func.id
    if name not in SAFE_FUNCTIONS:
        _fail(
            node,
            f"'{name}' is not a function a formula can call. "
            f"Available: {', '.join(sorted(SAFE_FUNCTIONS))}.",
        )
    if node.keywords:
        # The old evaluator dropped these silently, so `round(qty_kg,
        # ndigits=2)` ran as `round(qty_kg)` and returned a plausible number
        # that was not the one the formula asked for. The panel refuses it.
        _fail(node, "Keyword arguments are not allowed in a formula.")

    minimum, maximum = ARITY[name]
    argc = len(node.args)
    if argc < minimum or (maximum is not None and argc > maximum):
        expected = (
            f"exactly {minimum}" if minimum == maximum
            else f"at least {minimum}" if maximum is None
            else f"{minimum} or {maximum}"
        )
        _fail(node, f"{name}() takes {expected} argument(s) ({argc} given).")

    if name == "round" and argc == 2:
        ndigits = _int_literal(node.args[1])
        if ndigits is None:
            _fail(
                node.args[1],
                "round()'s second argument must be a whole number literal, "
                "e.g. round(x, 2).",
            )
        return _decimal(_guarded(node, round, _evaluate_node(node.args[0], variables), ndigits))

    arguments = [_evaluate_node(argument, variables) for argument in node.args]
    return _decimal(_guarded(node, SAFE_FUNCTIONS[name], *arguments))


def _guarded(node: ast.AST, function, *arguments):
    """Run `function`, converting a `decimal` failure into a located
    `FormulaError` rather than letting it reach `evaluate`'s backstop, which
    knows only the position of the whole expression."""
    try:
        return function(*arguments)
    except decimal.DivisionByZero:
        _fail(node, "Division by zero.")
    except decimal.DecimalException as exc:
        _fail(node, f"The arithmetic here is not valid ({type(exc).__name__}).")
    except (TypeError, ValueError) as exc:
        _fail(node, f"This call cannot be run: {exc}.")


def _decimal(value) -> Decimal:
    """One-argument `round(Decimal)` returns a Python `int`; everything else
    here already returns `Decimal`. `Decimal(int)` is exact — no `float` is
    ever constructed, per the repository-wide rule."""
    return value if isinstance(value, Decimal) else Decimal(value)


def _evaluate_node(node: ast.AST, variables: dict[str, Decimal]) -> Decimal:
    if isinstance(node, ast.Call):
        return _call(node, variables)

    if isinstance(node, ast.Constant):
        return _constant(node)

    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        _fail(
            node,
            f"'{node.id}' is not a value this formula can use. "
            f"Available: {', '.join(sorted(variables))}.",
        )

    if isinstance(node, ast.UnaryOp):
        operator = UNARY_OPERATORS.get(type(node.op))
        if operator is None:
            _fail(
                node,
                f"'{_OPERATOR_DESCRIPTIONS.get(type(node.op), type(node.op).__name__)}'"
                " is not an operator a formula can use.",
            )
        return _guarded(node, operator, _evaluate_node(node.operand, variables))

    if isinstance(node, ast.BinOp):
        operator = OPERATORS.get(type(node.op))
        if operator is None:
            _fail(
                node,
                f"'{_OPERATOR_DESCRIPTIONS.get(type(node.op), type(node.op).__name__)}'"
                " is not an operator a formula can use. Available: + - * /.",
            )
        left = _evaluate_node(node.left, variables)
        right = _evaluate_node(node.right, variables)
        return _guarded(node, operator, left, right)

    # **Refusal by default.** Before this line `_evaluate_node` had no `else`:
    # a node type it did not recognise fell off the end and returned `None`.
    # `1 < 2` did not raise — it evaluated to `None`, which became
    # `MetricResult.total`, which reached the wire as `"total": null` or blew
    # up several frames later in the roll-up with a `TypeError` naming a line
    # nowhere near the formula. Everything the panel refuses by omission from
    # `_PERMITTED_NODES`, this refuses by falling through to here.
    _fail(node, f"{_describe(node)} is not allowed in a formula.")


def evaluate(formula: str, variables: dict[str, Decimal]) -> Decimal:
    """§4.3. Evaluate one line's contribution; raise `FormulaError` otherwise.

    Raises `FormulaError` (§4.4, carrying `expression`, `line`, `column` and
    `reason`) on a syntax error, an undefined variable, forbidden syntax,
    division by zero or a non-finite result.
    """
    if not formula or not formula.strip():
        raise FormulaError("The formula is empty.", expression=formula or "")

    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        # `SyntaxError.offset` is 1-based; every other column in this system
        # is `col_offset`'s 0-based convention, and so is the panel's. A caret
        # one character to the right of the problem is worse than no caret.
        raise FormulaError(
            f"Syntax error: {exc.msg}",
            expression=formula,
            line=exc.lineno or 1,
            column=max((exc.offset or 1) - 1, 0),
        ) from exc

    try:
        value = _evaluate_node(tree.body, variables)
    except FormulaError as exc:
        # An inner node knows its own position but not the text it came from,
        # so `expression` is stamped once here on the way out.
        exc.expression = formula
        raise
    except Exception as exc:
        # Backstop, not the primary mechanism. Everything above raises a
        # located `FormulaError` of its own; anything reaching here is a bug
        # in this module, and it must still be contained rather than surfacing
        # as a bare `KeyError` from inside a public request.
        raise FormulaError(
            f"The formula could not be evaluated ({type(exc).__name__}).",
            expression=formula,
            line=getattr(tree.body, "lineno", 1),
            column=getattr(tree.body, "col_offset", 0),
        ) from exc

    if not value.is_finite():
        # §4.3 lists a non-finite *result* as a raise trigger in its own
        # right. No path reaches it today — non-finite literals are refused
        # above, division by zero raises, and `decimal` traps Overflow — but a
        # non-finite value that escapes into `MetricResult.total` is the same
        # silent-propagation failure as the `None` above, and the next
        # function added to `SAFE_FUNCTIONS` could open a path.
        raise FormulaError(
            "The formula produced a value that is not a finite number.",
            expression=formula,
            line=getattr(tree.body, "lineno", 1),
            column=getattr(tree.body, "col_offset", 0),
        )
    return value


#: §4.3 names this function `evaluate_expression`. `evaluate` is the name the
#: engine has used since it was written and every caller binds; the alias
#: exists so that a reader looking for the contract's name — or for the pair
#: `admin/expressions.py` forms with it — finds it.
evaluate_expression = evaluate
