"""Minimal, safe expression evaluator for primitive design parameters.

Only arithmetic over numbers is allowed: the AST whitelist rejects calls,
attribute access, subscripts, comprehensions and everything else, so template
expressions can never execute arbitrary code.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_SI_SUFFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class ExpressionError(ValueError):
    """Raised when a design parameter expression is invalid."""


def evaluate(expr: str, bindings: dict[str, float]) -> float:
    """Evaluate ``expr`` with numeric ``bindings``; only arithmetic is allowed."""
    tree = ast.parse(expr, mode="eval")
    return float(_eval(tree.body, dict(bindings)))


def _eval(node: ast.AST, bindings: dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in bindings:
            raise ExpressionError(f"Undefined parameter: {node.id!r}")
        return bindings[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, bindings), _eval(node.right, bindings))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval(node.operand, bindings))
    raise ExpressionError(f"Unsupported expression node: {type(node).__name__}")


def expr_names(expr: str) -> list[str]:
    """Return the sorted Name references in ``expr`` (for dependency resolution)."""
    tree = ast.parse(expr, mode="eval")
    return sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def parse_number(text: str) -> float:
    """Parse a numeric literal with an optional single-letter SI suffix."""
    t = str(text).strip()
    suffix = ""
    while t and t[-1].isalpha():
        suffix = t[-1] + suffix
        t = t[:-1]
    if suffix not in _SI_SUFFIXES:
        raise ExpressionError(f"Unknown SI suffix: {suffix!r}")
    return float(t) * _SI_SUFFIXES[suffix]
