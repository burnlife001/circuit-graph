"""Parametric design output: turn primitives into concrete circuits."""

from circuit_graph.design.expr import ExpressionError, evaluate, expr_names, parse_number
from circuit_graph.design.instantiate import (
    DesignError,
    DesignResult,
    DesignSpec,
    instantiate,
)

__all__ = [
    "DesignError",
    "DesignResult",
    "DesignSpec",
    "ExpressionError",
    "evaluate",
    "expr_names",
    "instantiate",
    "parse_number",
]
