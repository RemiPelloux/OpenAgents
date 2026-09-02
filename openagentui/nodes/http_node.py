"""``http`` node — direct outbound HTTP request, no LLM/tool indirection."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult
from openagentui.templating import render_dict

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0


def execute(ctx: NodeContext) -> NodeExecutionResult:
    url = ctx.rendered("url")
    if not url:
        return failed(ctx.node.id, "http node has no 'url' configured")

    method = str(ctx.data.get("method") or "GET").upper()
    headers = render_dict(
        ctx.data.get("headers") or {},
        variables=ctx.execution.variables,
        node_results=ctx.execution.node_results,
    )
    body = ctx.data.get("body")
    if isinstance(body, dict):
        body = render_dict(
            body,
            variables=ctx.execution.variables,
            node_results=ctx.execution.node_results,
        )
    elif isinstance(body, str):
        from openagentui.templating import render

        body = render(
            body,
            variables=ctx.execution.variables,
            node_results=ctx.execution.node_results,
        )

    request_input = {"method": method, "url": url, "headers": headers, "body": body}

    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            if isinstance(body, dict):
                response = client.request(method, url, headers=headers, json=body)
            else:
                response = client.request(method, url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        return failed(
            ctx.node.id, f"http request failed: {exc}", input_value=request_input
        )

    output: Any
    try:
        output = response.json()
    except ValueError:
        output = response.text

    result_payload = {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": output,
    }

    output_field = ctx.data.get("outputField")
    if output_field:
        ctx.set_variable(output_field, result_payload)

    if response.status_code >= 400:
        return failed(
            ctx.node.id,
            f"http {response.status_code}: {str(output)[:500]}",
            input_value=request_input,
        )
    return ok(ctx.node.id, result_payload, input_value=request_input)
