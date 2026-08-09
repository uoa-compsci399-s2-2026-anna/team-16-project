import ast
from decimal import Decimal
from engine.errors import FormulaError

OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
SAFE_FUNCTIONS = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
}

def _evaluate_node(node, variables):
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise FormulaError(
                "Invalid function"
            )
        function_name = node.func.id

        if function_name not in SAFE_FUNCTIONS:
            raise FormulaError(
                f"Function {function_name} is not allowed"
            )
        function = SAFE_FUNCTIONS[function_name]
        
        arguments = [
            _evaluate_node(arg, variables)
            for arg in node.args
        ]

        return Decimal(str(function(*arguments)))

    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        
        raise FormulaError()
    
    if isinstance(node, ast.UnaryOp):

        value = _evaluate_node(
            node.operand,
            variables
        )

        if isinstance(node.op, ast.USub):
            return -value

        if isinstance(node.op, ast.UAdd):
            return value

        raise FormulaError(
            "Unsupported unary operator"
        )

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, variables)
        right = _evaluate_node(node.right, variables)

        operator = OPERATORS[type(node.op)]

        return operator(left, right)

def evaluate(formula: str, variables: dict[str, Decimal]) -> Decimal:
    try:
        tree = ast.parse(
            formula,
            mode="eval"
        )

        return _evaluate_node(
            tree.body,
            variables
        )
    except Exception as e:
        raise FormulaError(str(e))