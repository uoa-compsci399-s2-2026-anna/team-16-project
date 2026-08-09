class EngineError(Exception):
    pass

class UnknownCodeError(EngineError):
    pass

class UnknownConstantError(EngineError):
    pass

class FormulaError(EngineError):
    pass