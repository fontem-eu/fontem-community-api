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


def _eval_expression(node: ast.Expression, names: dict):
    return _eval(node.body, names)


def _eval_constant(node: ast.Constant, _names: dict):
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise ValueError("only numbers are allowed")
    return node.value


def _eval_name(node: ast.Name, names: dict):
    if node.id not in names:
        raise ValueError(f"unknown name {node.id!r}; bind it via `values`")
    return names[node.id]


def _eval_binop(node: ast.BinOp, names: dict):
    op = _BINOPS.get(type(node.op))
    if op is None:
        raise ValueError(f"unsupported operator: {type(node.op).__name__}")
    left = _eval(node.left, names)
    right = _eval(node.right, names)
    if isinstance(node.op, ast.Pow) and (
            abs(_as_num(left)) > _MAX_POW or abs(_as_num(right)) > _MAX_POW):
        raise ValueError(f"exponentiation is bounded at {_MAX_POW}")
    return op(left, right)


def _eval_unary(node: ast.UnaryOp, names: dict):
    op = _UNARY.get(type(node.op))
    if op is None:
        raise ValueError(f"unsupported operator: {type(node.op).__name__}")
    return op(_eval(node.operand, names))


def _eval_call(node: ast.Call, names: dict):
    if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
        raise ValueError("only sum, avg, min, max, abs, round and len "
                         "may be called")
    if node.keywords:
        raise ValueError("keyword arguments are not supported")
    return _FUNCS[node.func.id](*[_eval(a, names) for a in node.args])


def _eval_list(node: ast.List, names: dict):
    return [_eval(e, names) for e in node.elts]


#: The whitelist IS this table: a node kind absent here is refused, whatever
#: Python's grammar would let it mean. Attribute access, comprehensions,
#: strings and lambdas are not entries on purpose.
_NODE_HANDLERS = {
    ast.Expression: _eval_expression,
    ast.Constant: _eval_constant,
    ast.Name: _eval_name,
    ast.BinOp: _eval_binop,
    ast.UnaryOp: _eval_unary,
    ast.Call: _eval_call,
    ast.List: _eval_list,
}


def _eval(node: ast.AST, names: dict):
    handler = _NODE_HANDLERS.get(type(node))
    if handler is None:
        raise ValueError(f"unsupported syntax: {type(node).__name__}")
    return handler(node, names)


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
