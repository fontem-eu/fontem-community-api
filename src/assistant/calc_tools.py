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

The accepted syntax is a small, well-known subset of Python rather than an
invented grammar: models already write it fluently, which is the point —
the first MiniMax eval runs showed a model burning rounds guessing at input
shapes the tool refused. Up to a few lines of assignments, `if`/`for`,
list comprehensions, and a fixed set of math functions. Execution is a
hand-rolled AST walk over a whitelist — `eval()`/`exec()` are not in this
file and must never be — bounded three ways: an interpreted-step budget and
a 2-second wall clock (loops terminate), and a cap on integer width and
list size (memory stays flat). A node kind absent from the handler table is
refused, whatever Python's grammar would let it mean: no attributes, no
imports, no strings, no lambdas, no while.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import re
import statistics
import time

CALC_TOOL_NAME = "mcp__gmr__calculate"

CALC_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": CALC_TOOL_NAME,
            "description": (
                "Evaluates a short Python-syntax calculation exactly and "
                "returns the result. Use this for EVERY derived figure — "
                "sums, differences, ratios, percentage changes, including "
                "intermediate totals — instead of computing in your head; "
                "quote the result it returns. Up to 6 lines: assignments, "
                "if/for, list comprehensions, numbers and lists, operators "
                "+ - * / // % ** and comparisons, and the functions sum, "
                "mean, avg, median, stdev, pstdev, min, max, abs, round, "
                "len, sqrt, floor, ceil, range. The result is the last "
                "expression line (or assign to `result`). Numbers from "
                "tool results can be bound by name via `values`; numeric "
                "strings are accepted there. No imports, strings, or "
                "attribute access."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. \"(a - b) / b * 100\" or "
                                       "\"big = [v for v in vals if v > 1e6]"
                                       "\\nsum(big) / sum(vals) * 100\"",
                    },
                    "values": {
                        "type": "object",
                        "description": "Name -> number or list of numbers "
                                       "(numeric strings accepted), e.g. "
                                       "{\"a\": 1287435, \"v\": [1, 2, 3]}",
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
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}

#: Enough for any honest calculation; a bound against pathological input.
_MAX_EXPR_CHARS = 1_000
_MAX_LINES = 6
_MAX_LIST_ITEMS = 10_000
#: Exponent bound: 10**10**10 is a denial of service, not arithmetic.
_MAX_POW = 1_000
#: Integer width bound — the memory cap. Repeated squaring in a loop turns
#: ints into gigabytes; ~20k bits (2.5KB per number) does not.
_MAX_INT_BITS = 20_000
#: Interpreted-step budget: every node visit and loop iteration is a tick.
_MAX_OPS = 100_000
#: Wall-clock bound, checked every few hundred ticks.
_MAX_SECONDS = 2.0

_NUMERIC_STR = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _bounded_range(*args):
    r = range(*(int(a) for a in args))
    if len(r) > _MAX_LIST_ITEMS:
        raise ValueError(f"range is bounded at {_MAX_LIST_ITEMS} items")
    return list(r)


_FUNCS = {
    "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "len": len, "avg": statistics.fmean, "mean": statistics.fmean,
    "median": statistics.median, "stdev": statistics.stdev,
    "pstdev": statistics.pstdev, "sqrt": math.sqrt, "floor": math.floor,
    "ceil": math.ceil, "range": _bounded_range,
}


class _Budget:
    """Step + wall-clock budget shared by one evaluation."""

    def __init__(self):
        self.ops = 0
        self.deadline = time.monotonic() + _MAX_SECONDS

    def tick(self):
        self.ops += 1
        if self.ops > _MAX_OPS:
            raise ValueError(f"calculation exceeds {_MAX_OPS} steps")
        if self.ops % 512 == 0 and time.monotonic() > self.deadline:
            raise ValueError(f"calculation exceeds {_MAX_SECONDS} seconds")


class _Frame:
    """One evaluation's variables and budget."""

    def __init__(self, names: dict):
        self.names = names
        self.budget = _Budget()


def _coerce_number(name: str, value: str):
    """A numeric string is a number the model read off a tool result.

    Tool results carry values as JSON strings more often than not, and some
    serializers (MiniMax's, observed in eval traces) keep them that way; a
    calculator that refuses "30862249.55" pushes the model back to in-head
    arithmetic, which is the failure this tool exists to end.
    """
    s = value.strip()
    if not _NUMERIC_STR.fullmatch(s):
        raise ValueError(f"{name}: {value!r} is not a number")
    return float(s)


def _check_value(name: str, value):
    if isinstance(value, bool):
        raise ValueError(f"{name}: booleans are not numbers here")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _coerce_number(name, value)
    if isinstance(value, dict) and set(value) == {"item"}:
        # Some tool-call serializers wrap every list in {"item": [...]}.
        return _check_value(name, value["item"])
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise ValueError(f"{name}: at most {_MAX_LIST_ITEMS} items")
        return [_check_value(f"{name}[]", v) for v in value]
    raise ValueError(f"{name}: expected a number or a list of numbers")


def _guard_size(result):
    if isinstance(result, int) and result.bit_length() > _MAX_INT_BITS:
        raise ValueError("intermediate value is too large")
    if isinstance(result, list) and len(result) > _MAX_LIST_ITEMS:
        raise ValueError(f"lists are bounded at {_MAX_LIST_ITEMS} items")
    return result


def _eval_constant(node: ast.Constant, _frame: _Frame):
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise ValueError("only numbers are allowed")
    return node.value


def _eval_name(node: ast.Name, frame: _Frame):
    if node.id not in frame.names:
        raise ValueError(f"unknown name {node.id!r}; assign it or bind it "
                         "via `values`")
    return frame.names[node.id]


def _eval_binop(node: ast.BinOp, frame: _Frame):
    op = _BINOPS.get(type(node.op))
    if op is None:
        raise ValueError(f"unsupported operator: {type(node.op).__name__}")
    left = _eval(node.left, frame)
    right = _eval(node.right, frame)
    if isinstance(node.op, ast.Pow) and (
            abs(_as_num(left)) > _MAX_POW or abs(_as_num(right)) > _MAX_POW):
        raise ValueError(f"exponentiation is bounded at {_MAX_POW}")
    return _guard_size(op(left, right))


def _eval_unary(node: ast.UnaryOp, frame: _Frame):
    op = _UNARY.get(type(node.op))
    if op is None:
        raise ValueError(f"unsupported operator: {type(node.op).__name__}")
    return op(_eval(node.operand, frame))


def _eval_compare(node: ast.Compare, frame: _Frame):
    left = _eval(node.left, frame)
    for op_node, comp in zip(node.ops, node.comparators):
        op = _COMPARE.get(type(op_node))
        if op is None:
            raise ValueError(
                f"unsupported comparison: {type(op_node).__name__}")
        right = _eval(comp, frame)
        if not op(left, right):
            return False
        left = right
    return True


def _eval_boolop(node: ast.BoolOp, frame: _Frame):
    is_and = isinstance(node.op, ast.And)
    value = None
    for part in node.values:
        value = _eval(part, frame)
        if is_and and not value:
            return value
        if not is_and and value:
            return value
    return value


def _eval_ifexp(node: ast.IfExp, frame: _Frame):
    return (_eval(node.body, frame) if _eval(node.test, frame)
            else _eval(node.orelse, frame))


def _eval_call(node: ast.Call, frame: _Frame):
    if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
        raise ValueError("only these functions may be called: "
                         + ", ".join(sorted(_FUNCS)))
    if node.keywords:
        raise ValueError("keyword arguments are not supported")
    return _guard_size(
        _FUNCS[node.func.id](*[_eval(a, frame) for a in node.args]))


def _eval_list(node: ast.List, frame: _Frame):
    return _guard_size([_eval(e, frame) for e in node.elts])


def _eval_listcomp(node: ast.ListComp, frame: _Frame):
    out: list = []

    def generate(gen_index: int):
        if gen_index == len(node.generators):
            out.append(_eval(node.elt, frame))
            _guard_size(out)
            return
        gen = node.generators[gen_index]
        if gen.is_async:
            raise ValueError("async comprehensions are not supported")
        if not isinstance(gen.target, ast.Name):
            raise ValueError("comprehension targets must be plain names")
        for item in _iterable(_eval(gen.iter, frame)):
            frame.budget.tick()
            frame.names[gen.target.id] = item
            if all(_eval(cond, frame) for cond in gen.ifs):
                generate(gen_index + 1)

    generate(0)
    return out


#: The whitelist IS this table: a node kind absent here is refused, whatever
#: Python's grammar would let it mean. Attribute access, subscripts,
#: strings, lambdas, while and imports are not entries on purpose.
_NODE_HANDLERS = {
    ast.Constant: _eval_constant,
    ast.Name: _eval_name,
    ast.BinOp: _eval_binop,
    ast.UnaryOp: _eval_unary,
    ast.Compare: _eval_compare,
    ast.BoolOp: _eval_boolop,
    ast.IfExp: _eval_ifexp,
    ast.Call: _eval_call,
    ast.List: _eval_list,
    ast.ListComp: _eval_listcomp,
}


def _eval(node: ast.AST, frame: _Frame):
    frame.budget.tick()
    handler = _NODE_HANDLERS.get(type(node))
    if handler is None:
        raise ValueError(f"unsupported syntax: {type(node).__name__}")
    return handler(node, frame)


def _iterable(value) -> list:
    if not isinstance(value, list):
        raise ValueError("only lists can be iterated")
    return value


def _assign_target(node: ast.AST) -> str:
    if not isinstance(node, ast.Name):
        raise ValueError("assignment targets must be plain names")
    if node.id in _FUNCS:
        raise ValueError(f"{node.id!r} is a function name; pick another")
    return node.id


def _stmt_expr(stmt: ast.Expr, frame: _Frame):
    return _eval(stmt.value, frame)


def _stmt_assign(stmt: ast.Assign, frame: _Frame):
    if len(stmt.targets) != 1:
        raise ValueError("one assignment target at a time")
    frame.names[_assign_target(stmt.targets[0])] = _eval(stmt.value, frame)


def _stmt_augassign(stmt: ast.AugAssign, frame: _Frame):
    name = _assign_target(stmt.target)
    op = _BINOPS.get(type(stmt.op))
    if op is None:
        raise ValueError(f"unsupported operator: {type(stmt.op).__name__}")
    if name not in frame.names:
        raise ValueError(f"unknown name {name!r}; assign it first")
    frame.names[name] = _guard_size(
        op(frame.names[name], _eval(stmt.value, frame)))


def _stmt_if(stmt: ast.If, frame: _Frame):
    body = stmt.body if _eval(stmt.test, frame) else stmt.orelse
    return _exec_body(body, frame)


def _stmt_for(stmt: ast.For, frame: _Frame):
    if stmt.orelse:
        raise ValueError("for/else is not supported")
    name = _assign_target(stmt.target)
    last = None
    for item in _iterable(_eval(stmt.iter, frame)):
        frame.budget.tick()
        frame.names[name] = item
        value = _exec_body(stmt.body, frame)
        if value is not None:
            last = value
    return last


#: Same whitelist-is-the-table rule as _NODE_HANDLERS, for statements.
_STMT_HANDLERS = {
    ast.Expr: _stmt_expr,
    ast.Assign: _stmt_assign,
    ast.AugAssign: _stmt_augassign,
    ast.If: _stmt_if,
    ast.For: _stmt_for,
}


def _exec_stmt(stmt: ast.stmt, frame: _Frame):
    """One statement. Returns the value of an expression statement."""
    frame.budget.tick()
    handler = _STMT_HANDLERS.get(type(stmt))
    if handler is None:
        raise ValueError(f"unsupported statement: {type(stmt).__name__}")
    return handler(stmt, frame)


def _exec_body(body: list[ast.stmt], frame: _Frame):
    last = None
    for stmt in body:
        value = _exec_stmt(stmt, frame)
        if value is not None:
            last = value
    return last


def _as_num(v) -> float:
    if isinstance(v, list):
        raise ValueError("a list cannot be an exponent")
    return float(v)


def _prevalidate(expression: str) -> str | None:
    if not expression:
        return "expression is required"
    if len(expression) > _MAX_EXPR_CHARS:
        return f"expression exceeds {_MAX_EXPR_CHARS} characters"
    lines = [line for line in expression.splitlines() if line.strip()]
    if len(lines) > _MAX_LINES:
        return f"at most {_MAX_LINES} lines"
    return None


def execute(args: dict) -> str:
    """One evaluation. Always JSON text, never an exception."""
    expression = str(args.get("expression") or "").strip()
    error = _prevalidate(expression)
    if error:
        return json.dumps({"error": error})
    try:
        names = {k: _check_value(k, v)
                 for k, v in (args.get("values") or {}).items()}
        frame = _Frame(names)
        tree = ast.parse(expression, mode="exec")
        result = _exec_body(tree.body, frame)
        if result is None:
            result = frame.names.get("result")
        if result is None:
            raise ValueError("end with an expression line, or assign to "
                             "`result`")
        if isinstance(result, list):
            raise ValueError("the result is a list, not a number — "
                             "aggregate it (sum, mean, …)")
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError("the result is not a finite number")
    except ZeroDivisionError:
        return json.dumps({"error": "division by zero"})
    except RecursionError:
        return json.dumps({"error": "expression is nested too deeply"})
    except (ValueError, SyntaxError, TypeError, OverflowError,
            statistics.StatisticsError) as exc:
        return json.dumps({"error": str(exc) or type(exc).__name__})
    return json.dumps({"expression": expression, "result": result})
