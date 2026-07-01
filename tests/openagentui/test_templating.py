"""Tests for openagentui.templating placeholder substitution."""

from openagentui.schema import NodeExecutionResult
from openagentui.templating import render, render_dict


def test_render_non_string_passthrough():
    assert render(42, variables={}, node_results={}) == 42
    assert render(None, variables={}, node_results={}) is None


def test_render_simple_variable():
    assert render("hello {{ name }}!", variables={"name": "world"}, node_results={}) == "hello world!"


def test_render_missing_variable_left_as_placeholder():
    text = "{{ missing }}"
    assert render(text, variables={}, node_results={}) == text


def test_render_dict_variable_stringified_as_json():
    result = render("{{ payload }}", variables={"payload": {"a": 1}}, node_results={})
    assert result == '{"a": 1}'


def test_render_nested_variable_path():
    variables = {"user": {"profile": {"email": "a@b.com"}}}
    assert render("{{ user.profile.email }}", variables=variables, node_results={}) == "a@b.com"


def test_render_node_output_reference():
    node_results = {"step1": NodeExecutionResult(node_id="step1", status="completed", output={"brief": "hi"})}
    assert render("{{ nodes.step1.output.brief }}", variables={}, node_results=node_results) == "hi"


def test_render_node_output_missing_node():
    text = "{{ nodes.unknown.output }}"
    assert render(text, variables={}, node_results={}) == text


def test_render_dict_helper():
    out = render_dict(
        {"greeting": "hi {{ name }}", "count": 3},
        variables={"name": "bob"},
        node_results={},
    )
    assert out == {"greeting": "hi bob", "count": 3}
