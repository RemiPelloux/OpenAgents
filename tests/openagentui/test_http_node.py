"""Tests for the ``http`` node executor (outbound httpx request)."""

import httpx
import pytest

from openagentui.nodes import http_node
from openagentui.nodes.base import NodeContext
from openagentui.schema import WorkflowExecution, WorkflowNode


def _ctx(data: dict, variables: dict | None = None) -> NodeContext:
    node = WorkflowNode(id="n1", type="http", data=data)
    execution = WorkflowExecution(id="exec1", workflow_id="wf1", variables=dict(variables or {}))
    return NodeContext(node=node, execution=execution)


def _mock_client(handler):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, headers=None, json=None, content=None):
            return handler(method, url, headers, json, content)

    return _FakeClient


def test_http_missing_url_fails():
    ctx = _ctx({})
    result = http_node.execute(ctx)
    assert result.status == "failed"
    assert "url" in result.error


def test_http_success_returns_json_body(monkeypatch):
    def handler(method, url, headers, json_body, content):
        request = httpx.Request(method, url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(http_node.httpx, "Client", _mock_client(handler))
    ctx = _ctx({"url": "https://example.com/api", "method": "GET", "outputField": "resp"})
    result = http_node.execute(ctx)
    assert result.status == "completed"
    assert result.output["status"] == 200
    assert result.output["body"] == {"ok": True}
    assert ctx.execution.variables["resp"]["body"] == {"ok": True}


def test_http_renders_templated_url_and_body(monkeypatch):
    captured = {}

    def handler(method, url, headers, json_body, content):
        captured["url"] = url
        captured["json"] = json_body
        request = httpx.Request(method, url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(http_node.httpx, "Client", _mock_client(handler))
    ctx = _ctx(
        {"url": "https://example.com/{{ path }}", "method": "POST", "body": {"name": "{{ user }}"}},
        {"path": "users", "user": "bob"},
    )
    result = http_node.execute(ctx)
    assert result.status == "completed"
    assert captured["url"] == "https://example.com/users"
    assert captured["json"] == {"name": "bob"}


def test_http_error_status_fails(monkeypatch):
    def handler(method, url, headers, json_body, content):
        request = httpx.Request(method, url)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setattr(http_node.httpx, "Client", _mock_client(handler))
    ctx = _ctx({"url": "https://example.com/missing"})
    result = http_node.execute(ctx)
    assert result.status == "failed"
    assert "404" in result.error


def test_http_transport_error_fails(monkeypatch):
    class _RaisingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, *args, **kwargs):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(http_node.httpx, "Client", _RaisingClient)
    ctx = _ctx({"url": "https://unreachable.example"})
    result = http_node.execute(ctx)
    assert result.status == "failed"
    assert "http request failed" in result.error
