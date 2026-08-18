import pytest

from circuit_graph.design.expr import (
    ExpressionError,
    evaluate,
    expr_names,
    parse_number,
)


def test_evaluate_arithmetic_with_bindings() -> None:
    result = evaluate("(vin - vout) * r_bottom / vout", {"vin": 12, "vout": 5, "r_bottom": 10000})
    assert result == 14000.0


def test_evaluate_unary_minus() -> None:
    assert evaluate("-vout + 10", {"vout": 5}) == 5.0


def test_parse_number_si_suffixes() -> None:
    assert parse_number("4.7k") == 4700.0
    assert parse_number("10u") == pytest.approx(1e-5)
    assert parse_number("100") == 100.0
    assert parse_number("2M") == 2e6


def test_evaluate_undefined_name_raises() -> None:
    with pytest.raises(ExpressionError, match="Undefined"):
        evaluate("vout / 2", {})


def test_evaluate_rejects_arbitrary_code() -> None:
    with pytest.raises(ExpressionError):
        evaluate("__import__('os').system('echo pwned')", {})
    with pytest.raises(ExpressionError):
        evaluate("(lambda: 1)()", {})


def test_evaluate_rejects_unknown_suffix() -> None:
    with pytest.raises(ExpressionError, match="SI suffix"):
        parse_number("10x")


def test_expr_names_extracts_references() -> None:
    assert expr_names("(vin - vout) * r_bottom / vout") == ["r_bottom", "vin", "vout"]
