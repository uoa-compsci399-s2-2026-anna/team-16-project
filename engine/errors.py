class EngineError(Exception):
    pass

class UnknownCodeError(EngineError):
    pass

class UnknownConstantError(EngineError):
    pass

class FormulaError(EngineError):
    """A staff-written expression the evaluator refused (contract §4.4).

    §4.4 requires **structured attributes, not a pre-formatted message**,
    because §9.1 gives this error two presentations: opaque for a public
    request (the expression is never echoed to someone who did not write it)
    and fully located for an authenticated dry run, where a staff member
    tuning a formula cannot fix what they cannot see. A single string forces
    the API to choose one presentation for both.

    `api/errors.py::engine_problem` reads all four by `getattr`, and E's
    dry-run screen renders them beside the field. `line` and `column` follow
    `ast`'s convention — 1-based line, **0-based** column — which is the same
    convention `admin/expressions.py::ExpressionError` uses, so the located
    error a staff member sees when *saving* a formula and the one they see
    when *running* it point at the same character.

    `expression` is stamped by `evaluate()` on the way out rather than at
    each raise site: an inner node knows its own position but not the text it
    came from.
    """

    def __init__(
        self,
        reason: str = "",
        *,
        expression: str = "",
        line: int = 1,
        column: int = 0,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.expression = expression
        self.line = line
        self.column = column


class BundleFormatError(EngineError):
    """`FactorBundle.from_json()` received malformed input (contract §4.4).

    Mapped by the API to `VALIDATION_ERROR` (400). It exists so that a
    malformed bundle never surfaces as a bare `KeyError` or `TypeError`: the
    panel's dry-run view (§6.2.1) shows this message to a staff member who
    pasted a bundle, and a `KeyError` there is a 500 with no actionable text.

    Distinct from `validate()`, which reports *internally inconsistent*
    bundles and never raises. Malformed means the document could not be read
    at all; invalid means it was read and its parts do not agree.
    """