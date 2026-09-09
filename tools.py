import ast
import math
import operator
from datetime import datetime, timezone

_ALLOWED = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _calc(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED:
        return _ALLOWED[type(node.op)](_calc(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        value = _ALLOWED[type(node.op)](left, right)
        if not math.isfinite(value):
            raise ValueError("Non-finite result")
        return value
    raise ValueError("Unsupported expression")


def calculator(expression: str):
    tree = ast.parse(expression, mode="eval")
    return _calc(tree.body)


def now_utc():
    return datetime.now(timezone.utc).isoformat()

TOOLS = {
    "calculator": {
        "description": "Safely calculate an arithmetic expression.",
        "input": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        "run": lambda a: str(calculator(a["expression"])),
    },
    "time": {
        "description": "Get the current UTC time.",
        "input": {"type": "object", "properties": {}},
        "run": lambda a: now_utc(),
    },
}


def tool_catalog():
    return [{"name": k, "description": v["description"], "input_schema": v["input"]} for k, v in TOOLS.items()]


def run_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown tool")
    return TOOLS[name]["run"](arguments)
