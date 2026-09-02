"""``transform`` node — runs a Python snippet in OpenAgents' code sandbox.

Replaces the upstream E2B-hosted transform step. The script receives the
current workflow variables as an ``INPUT`` dict (injected as a literal at
the top of the script) and should ``print(json.dumps(result))`` to produce
a structured output; anything else printed is still captured as raw text.
"""

from __future__ import annotations

import json
import logging

from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult

logger = logging.getLogger(__name__)


def _build_script(user_code: str, input_value: dict) -> str:
    preamble = f"import json\nINPUT = {json.dumps(input_value)}\n\n"
    return preamble + user_code


def execute(ctx: NodeContext) -> NodeExecutionResult:
    script = ctx.data.get("transformScript") or ctx.data.get("code")
    if not script:
        return failed(ctx.node.id, "transform node has no 'transformScript' configured")

    input_value = dict(ctx.execution.variables)
    full_script = _build_script(script, input_value)

    try:
        from tools.code_execution_tool import execute_code
    except (
        Exception
    ) as exc:  # pragma: no cover - sandbox unavailable in this environment
        return failed(
            ctx.node.id, f"code sandbox unavailable: {exc}", input_value=input_value
        )

    try:
        raw_result = execute_code(
            full_script, task_id=f"openagentui-{ctx.execution.id}-{ctx.node.id}"
        )
    except Exception as exc:
        logger.exception("openagentui: transform node %s failed", ctx.node.id)
        return failed(
            ctx.node.id, f"transform execution failed: {exc}", input_value=input_value
        )

    try:
        parsed = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return failed(
            ctx.node.id,
            f"malformed sandbox response: {raw_result[:500]}",
            input_value=input_value,
        )

    if parsed.get("error"):
        return failed(ctx.node.id, str(parsed["error"]), input_value=input_value)
    if parsed.get("status") not in (None, "success", "completed"):
        return failed(
            ctx.node.id,
            f"transform script {parsed.get('status')}: {parsed.get('output', '')}",
            input_value=input_value,
        )

    stdout_text = (parsed.get("output") or "").strip()
    output = stdout_text
    if stdout_text:
        try:
            output = json.loads(stdout_text)
        except (json.JSONDecodeError, TypeError):
            output = stdout_text

    output_field = ctx.data.get("outputField")
    if output_field:
        ctx.set_variable(output_field, output)

    return ok(ctx.node.id, output, input_value=input_value)
