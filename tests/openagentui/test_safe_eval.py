"""Tests for openagentui.safe_eval — the constrained if-else/while evaluator."""

import pytest

from openagentui.safe_eval import UnsafeExpressionError, evaluate_condition


def test_empty_expression_is_false():
    assert evaluate_condition("", {}) is False
    assert evaluate_condition(None, {}) is False


def test_simple_comparison():
    assert evaluate_condition("count > 3", {"count": 5}) is True
    assert evaluate_condition("count > 3", {"count": 1}) is False


def test_boolean_logic():
    assert evaluate_condition("a and b", {"a": True, "b": True}) is True
    assert evaluate_condition("a or b", {"a": False, "b": True}) is True
    assert evaluate_condition("not a", {"a": False}) is True


def test_in_operator():
    assert evaluate_condition("'x' in items", {"items": ["x", "y"]}) is True
    assert evaluate_condition("'z' not in items", {"items": ["x", "y"]}) is True


def test_dotted_attribute_access_into_dict():
    variables = {"duplicate_check": {"duplicate": True}}
    assert evaluate_condition("duplicate_check.duplicate", variables) is True
    assert evaluate_condition("duplicate_check.missing_key", variables) is False


def test_subscript_access():
    assert evaluate_condition("items[0] == 'x'", {"items": ["x", "y"]}) is True


def test_arithmetic():
    assert evaluate_condition("count + 1 == 6", {"count": 5}) is True


def test_unsafe_call_rejected():
    with pytest.raises(UnsafeExpressionError):
        evaluate_condition("__import__('os').system('echo hi')", {})


def test_unsafe_syntax_rejected():
    with pytest.raises(UnsafeExpressionError):
        evaluate_condition("def foo(): pass", {})


def test_missing_variable_is_none():
    assert evaluate_condition("missing == None", {}) is True
