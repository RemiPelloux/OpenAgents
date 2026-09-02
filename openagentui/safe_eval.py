"""Constrained expression evaluator for ``if-else`` / ``while`` node conditions.

Workflow authors write conditions like ``status == "provisioned"`` or
``count < 5 and has_email``. Rather than a bare ``eval()`` (arbitrary code
execution against a workflow file that may have been imported from
anywhere), this walks a whitelisted AST subset: comparisons, boolean
logic, `in`/`not in`, arithmetic on numbers/strings, and variable lookups.
No attribute access, no calls, no imports, no comprehensions.
"""

from __future__ import annotations

import ast
from typing import Any, Dict

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_ALLOWED_COMPARE = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)
_ALLOWED_BOOLOP = (ast.And, ast.Or)
_ALLOWED_UNARY = (ast.Not, ast.USub, ast.UAdd)


class UnsafeExpressionError(ValueError):
    """Raised when a condition uses a disallowed language construct."""


def _eval_node(node: ast.AST, variables: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return variables.get(node.id)
    if isinstance(node, ast.Attribute):
        # Dotted access into JSON-shaped tool/agent output, e.g.
        # ``duplicate_check.duplicate`` where ``duplicate_check`` is a dict
        # variable set via a node's ``outputField``. Real Python attribute
        # access (methods, dunders, class internals) is never reachable
        # here since the base is always evaluated through this same
        # restricted walker, never through ``getattr`` on live objects.
        base = _eval_node(node.value, variables)
        if isinstance(base, dict):
            return base.get(node.attr)
        return None
    if isinstance(node, ast.Subscript):
        base = _eval_node(node.value, variables)
        key = _eval_node(node.slice, variables)
        if isinstance(base, (dict, list, tuple, str)):
            try:
                return base[key]
            except (KeyError, IndexError, TypeError):
                return None
        return None
    if isinstance(node, ast.List):
        return [_eval_node(elt, variables) for elt in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, variables) for elt in node.elts)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, _ALLOWED_BOOLOP):
        values = [_eval_node(v, variables) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
        operand = _eval_node(node.operand, variables)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        ops = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.Mod: lambda a, b: a % b,
        }
        return ops[type(node.op)](left, right)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, _ALLOWED_COMPARE):
                raise UnsafeExpressionError(
                    f"disallowed comparator: {type(op).__name__}"
                )
            right = _eval_node(comparator, variables)
            result = {
                ast.Eq: lambda a, b: a == b,
                ast.NotEq: lambda a, b: a != b,
                ast.Lt: lambda a, b: a < b,
                ast.LtE: lambda a, b: a <= b,
                ast.Gt: lambda a, b: a > b,
                ast.GtE: lambda a, b: a >= b,
                ast.In: lambda a, b: a in b,
                ast.NotIn: lambda a, b: a not in b,
            }[type(op)](left, right)
            if not result:
                return False
            left = right
        return True
    raise UnsafeExpressionError(f"disallowed expression node: {type(node).__name__}")


def evaluate_condition(expression: str, variables: Dict[str, Any]) -> bool:
    """Evaluate a boolean condition string against workflow variables.

    Raises ``UnsafeExpressionError`` for unparseable input or disallowed
    syntax; raises ``ValueError``/``TypeError`` for legitimate runtime
    errors (e.g. comparing incompatible types) exactly like a normal
    Python expression would.
    """
    expression = (expression or "").strip()
    if not expression:
        return False
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"invalid condition syntax: {exc}") from exc
    return bool(_eval_node(tree, variables))
