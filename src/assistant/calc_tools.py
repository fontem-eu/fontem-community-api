"""Deterministic arithmetic, as a tool call.

Two reasons this exists, and the second is the interesting one:

Models do arithmetic in their heads and get it nearly right — a sum off in
the fourth digit, a percentage change with the sign of the denominator
wrong. On a platform whose whole claim is that figures trace to a source,
"nearly right" is indistinguishable from fabricated.

And the eval harness's grounding check treats every tool RESULT as
evidence. A number computed here is therefore a number the scorer can see
the provenance of, where the same number computed in the model's head reads
as invented — that exact false-accusation class (correct labelled
arithmetic scored as fabrication) turned a full eval run's grounding score
to 0%. The fix is not a cleverer scorer; it is giving arithmetic a witness.

Evaluation is a hand-rolled AST walk over a whitelist. `eval()` is not in
this file and must never be.
"""
from __future__ import annotations

import ast
import json
import operator

CALC_TOOL_NAME = "mcp__gmr__calculate"

CALC_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": CALC_TOOL_NAME,
            "description": (
                "Evaluates one arithmetic expression exactly and returns "
                "the result. Use this for EVERY derived figure — sums, "
                "differences, ratios, percentage changes — instead of "
                "computing in your head; quote the result it returns. "
                "Numbers, + - * / // % **, parentheses, and the functions "
                "sum, avg, min, max, abs, round, len over lists. Values "
                "from tool results can be bound by name via `values`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. \"(a - b) / b * 100\" or "
                                       "\"sum(v) / len(v)\"",
                    },
                    "values": {
                        "type": "object",
                        "description": "Name -> number or list of numbers, "
                                       "e.g. {\"a\": 1287435, \"v\": "
                                       "[1, 2, 3]}",
                    },
                },
                "required": ["expression"],
            },
        },
    },
]

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCS = {
    "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "len": len, "avg": lambda xs: sum(xs) / len(xs),
}

#: Enough for any honest expression; a bound against pathological input.
_MAX_EXPR_CHARS = 500
_MAX_LIST_ITEMS = 10_000
#: Exponent bound: 10**10**10 is a denial of service, not arithmetic.
_MAX_POW = 1_000


def _check_value(name: str, value):
    if isinstance(value, bool):
        raise ValueError(f"{name}: booleans are not numbers here")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise ValueError(f"{name}: at most {_MAX_LIST_ITEMS} items")
        return [_check_value(f"{name}[]", v) for v in value]
    raise ValueError(f"{name}: expected a number or a list of numbers")


# An AST visitor is one return per node kind by nature; collapsing them
# into a dispatch table would hide the whitelist this module exists to
# make auditable.
# pylint: disable-next=too-many-return-statements
def _eval(node: ast.AST, names: dict):
    if isinstance(node, ast.Expression):
        return _eval(node.body, names)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            raise ValueError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise ValueError(f"unknown name {node.id!r}; bind it via "
                             f"`values`")
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval(node.left, names)
        right = _eval(node.right, names)
        if isinstance(node.op, ast.Pow) and (
                abs(_as_num(left)) > _MAX_POW or abs(_as_num(right)) > _MAX_POW):
            raise ValueError(f"exponentiation is bounded at {_MAX_POW}")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, names))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("only sum, avg, min, max, abs, round and len "
                             "may be called")
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        args = [_eval(a, names) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, ast.List):
        return [_eval(e, names) for e in node.elts]
    raise ValueError(f"unsupported syntax: {type(node).__name__}")


def _as_num(v) -> float:
    if isinstance(v, list):
        raise ValueError("a list cannot be an exponent")
    return float(v)


def execute(args: dict) -> str:
    """One evaluation. Always JSON text, never an exception."""
    expression = str(args.get("expression") or "").strip()
    if not expression:
        return json.dumps({"error": "expression is required"})
    if len(expression) > _MAX_EXPR_CHARS:
        return json.dumps(
            {"error": f"expression exceeds {_MAX_EXPR_CHARS} characters"})
    try:
        names = {k: _check_value(k, v)
                 for k, v in (args.get("values") or {}).items()}
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree, names)
        if isinstance(result, list):
            raise ValueError("the expression evaluates to a list, not a "
                             "number — aggregate it (sum, avg, …)")
    except ZeroDivisionError:
        return json.dumps({"error": "division by zero"})
    except (ValueError, SyntaxError, TypeError, OverflowError) as exc:
        return json.dumps({"error": str(exc) or type(exc).__name__})
    return json.dumps({"expression": expression, "result": result})
