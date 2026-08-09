from engine.errors import EngineError
from engine.errors import UnknownCodeError
from engine.errors import UnknownConstantError
from engine.errors import FormulaError

def test_unknown_code_error():
    assert issubclass(UnknownCodeError, EngineError)

def test_unknown_constant_error():
    assert issubclass(UnknownConstantError, EngineError)

def test_formula_error():
    assert issubclass(FormulaError, EngineError)

def test_engine_error():
    assert issubclass(EngineError, Exception)