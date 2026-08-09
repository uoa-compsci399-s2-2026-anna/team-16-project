class EngineError(Exception):
    pass

class UnknownCodeError(EngineError):
    pass

class UnknownConstantError(EngineError):
    pass

class FormulaError(EngineError):
    pass


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