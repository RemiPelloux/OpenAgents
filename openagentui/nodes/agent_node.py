"""``agent`` node — runs an LLM turn through OpenAgents' own agent loop.

This is the crux of "full native integration": rather than a separate
LangGraph executor calling the Anthropic/OpenAI SDKs directly (the upstream
behavior), the node reuses ``run_agent.AIAgent`` exactly the way
``batch_runner.py`` does for headless single-prompt runs — so an agent node
gets OpenAgents' already-configured model credentials, and its ``tools``
field selects real OpenAgents toolsets (not Arcade/Firecrawl MCP ids).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = 25


def _extract_final_text(messages: List[Dict[str, Any]]) -> str:
    """Return the text of the last assistant message in a run_conversation() result."""
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") in ("text", None)
            ]
            text = "".join(parts).strip()
            if text:
                return text
    return ""


def _coerce_output(text: str, output_format: Optional[str]) -> Any:
    if output_format == "json" and text:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "openagentui: agent node requested JSON output but got non-JSON text"
            )
    return text


def execute(ctx: NodeContext) -> NodeExecutionResult:
    from openagentui.tool_catalog import ensure_tools_loaded

    instructions = ctx.rendered("instructions") or ctx.rendered("systemPrompt")
    if not instructions:
        return failed(ctx.node.id, "agent node has no 'instructions' to run")

    model = ctx.data.get("model") or ""
    toolsets = [t for t in (ctx.data.get("tools") or []) if isinstance(t, str)]
    output_format = ctx.data.get("outputFormat")
    max_iterations = int(ctx.data.get("maxIterations") or _DEFAULT_MAX_ITERATIONS)

    ensure_tools_loaded()

    try:
        from run_agent import AIAgent
    except (
        Exception
    ) as exc:  # pragma: no cover - import failure is an environment problem
        return failed(
            ctx.node.id,
            f"could not load OpenAgents agent runtime: {exc}",
            input_value=instructions,
        )

    from openagentui.llm_defaults import resolve_agent_runtime_kwargs

    runtime_kwargs = resolve_agent_runtime_kwargs(model if model else None)
    resolved_model = runtime_kwargs.get("model") or model

    try:
        agent = AIAgent(
            model=resolved_model,
            enabled_toolsets=toolsets or None,
            max_iterations=max_iterations,
            save_trajectories=False,
            verbose_logging=False,
            skip_context_files=True,
            skip_memory=True,
            **{k: v for k, v in runtime_kwargs.items() if k not in ("model",)},
        )
        result = agent.run_conversation(
            instructions, task_id=f"openagentui-{ctx.execution.id}-{ctx.node.id}"
        )
    except Exception as exc:
        logger.exception("openagentui: agent node %s failed", ctx.node.id)
        return failed(
            ctx.node.id, f"agent execution failed: {exc}", input_value=instructions
        )

    text = _extract_final_text(result.get("messages") or [])
    output = _coerce_output(text, output_format)

    output_field = ctx.data.get("outputField")
    if output_field:
        ctx.set_variable(output_field, output)

    node_result = ok(ctx.node.id, output, input_value=instructions)
    return node_result
