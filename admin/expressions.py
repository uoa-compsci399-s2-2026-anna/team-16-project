"""Static validation of a staff-written formula. Contract §4.3.

A formula is text a staff member types and the engine then executes on every
calculation. Saved broken, it surfaces as a FORMULA_ERROR 500 to a member of
the public — which is why §9.1 gives that error two presentations, one for
staff and one for everyone else. This catches what can be caught before the
row is saved.

Deliberately independent of A's engine. It checks syntax, node types and
names; it does not evaluate. Whether an expression divides by zero on real
data is what a dry run is for.

**Whitelist, not blacklist.** Only node types named here are permitted, so a
construct nobody anticipated is refused by default rather than admitted by
default. That is the property that makes this safe to point at text a
browser submitted.
"""

import ast
import math
from collections.abc import Iterable

#: Contract §4.3. Available on every line, whatever the factor set contains.
#: `const_GWP_CH4` is the special binding the engine resolves to the 20- or
#: 100-year value according to the request's gwp_horizon, so a formula never
#: hard-codes a horizon.
BASE_VARIABLES = frozenset({"qty_kg", "upstream", "downstream", "const_GWP_CH4"})

#: Contract §4.3. The entire list.
PERMITTED_FUNCTIONS = frozenset({"min", "max", "abs", "round"})

_PERMITTED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Call,
    ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
)


class ExpressionError(Exception):
    """A formula that cannot be saved, with where in the text it went wrong.

    The position is shown beside the field in the panel: "somewhere in this
    text" is not useful to someone debugging an expression.
    """

    def __init__(self, message: str, line: int = 1, column: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


def validate_expression(expression: str, *, constant_codes: Iterable[str]) -> None:
    """Raise ExpressionError unless `expression` is one the engine can run.

    `constant_codes` is the set of `constant.code` values belonging to the
    same factor set — a `const_X` reference resolves against those, so the
    same expression can be valid in one version and invalid in another.
    """
    if not expression or not expression.strip():
        raise ExpressionError("The formula is empty.")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(
            f"Syntax error: {exc.msg}",
            line=exc.lineno or 1,
            column=max((exc.offset or 1) - 1, 0),
        ) from exc

    permitted_names = set(BASE_VARIABLES) | {
        f"const_{code}" for code in constant_codes
    }

    # Names that are the function-half of a permitted Call must not be
    # checked against permitted_names — ast.walk visits them separately from
    # the Call node itself, and they are not values, they are call targets.
    call_function_name_ids = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _check_call(node)
            continue
        if not isinstance(node, _PERMITTED_NODES):
            raise ExpressionError(
                f"{_describe(node)} is not allowed in a formula.",
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0),
            )
        if isinstance(node, ast.Constant):
            _check_constant(node)
        if isinstance(node, ast.Name) and id(node) not in call_function_name_ids:
            if node.id not in permitted_names:
                raise ExpressionError(
                    f"'{node.id}' is not a value a formula can use. "
                    f"Available: {', '.join(sorted(permitted_names))}.",
                    line=node.lineno, column=node.col_offset,
                )


def _check_constant(node: ast.Constant) -> None:
    """§4.3 permits only decimal-number literals.

    ast.Constant covers every Python literal — strings, bytes, None,
    complex, Ellipsis and bool as well as int/float. bool is an int
    subclass, so it must be excluded explicitly. A non-finite float
    (inf/nan, reachable via a literal like 1e999) parses and evaluates
    fine but only fails later at Decimal conversion or JSON
    serialisation, after the number has been computed — so it is
    refused here instead.
    """
    value = node.value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExpressionError(
            f"{_describe_value(value)} is not a decimal number a formula can use.",
            line=node.lineno, column=node.col_offset,
        )


def _describe_value(value: object) -> str:
    return f"{value!r} ({type(value).__name__})"


def _check_call(node: ast.Call) -> None:
    """A call is permitted only to one of the four named functions."""
    if not isinstance(node.func, ast.Name):
        raise ExpressionError(
            "Only min, max, abs and round may be called in a formula.",
            line=node.lineno, column=node.col_offset,
        )
    if node.func.id not in PERMITTED_FUNCTIONS:
        raise ExpressionError(
            f"'{node.func.id}' is not a function a formula can call. "
            f"Available: {', '.join(sorted(PERMITTED_FUNCTIONS))}.",
            line=node.lineno, column=node.col_offset,
        )
    if node.keywords:
        raise ExpressionError(
            "Keyword arguments are not allowed in a formula.",
            line=node.lineno, column=node.col_offset,
        )


_DESCRIPTIONS = {
    ast.Attribute: "Attribute access", ast.Subscript: "Subscripting",
    ast.Lambda: "A function definition", ast.ListComp: "A comprehension",
    ast.SetComp: "A comprehension", ast.DictComp: "A comprehension",
    ast.GeneratorExp: "A comprehension", ast.IfExp: "A conditional",
    ast.NamedExpr: "Assignment", ast.Compare: "A comparison",
    ast.BoolOp: "A boolean operator", ast.List: "A list",
    ast.Tuple: "A tuple", ast.Dict: "A dictionary", ast.Set: "A set",
}


def _describe(node: ast.AST) -> str:
    return _DESCRIPTIONS.get(type(node), type(node).__name__)
