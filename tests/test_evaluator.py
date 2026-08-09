from decimal import Decimal
from engine.evaluator import evaluate
from engine.errors import FormulaError
import pytest

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