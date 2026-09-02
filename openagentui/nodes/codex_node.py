"""``codex`` node — headless Codex CLI engineering turn."""

from __future__ import annotations

from openagentui.codex_runner import run_codex_headless
from openagentui.nodes.base import NodeContext, failed, ok
from openagentui.schema import NodeExecutionResult


def execute(ctx: NodeContext) -> NodeExecutionResult:
    prompt = ctx.rendered("prompt") or ctx.rendered("instructions")
    if not prompt:
        return failed(ctx.node.id, "codex node has no prompt")

    sandbox = str(ctx.data.get("sandbox") or "workspace-write")
    full_auto = bool(ctx.data.get("fullAuto"))
    timeout = int(ctx.data.get("timeoutSeconds") or 3600)
    cwd = ctx.rendered("cwd") or None

    try:
        result = run_codex_headless(
            prompt,
            cwd=cwd,
            sandbox=sandbox,
            full_auto=full_auto,
            timeout_seconds=timeout,
        )
    except Exception as exc:
        return failed(ctx.node.id, f"codex exec failed: {exc}")

    if not result.get("ok"):
        return failed(
            ctx.node.id,
            result.get("summary") or "codex exited with error",
            input_value=prompt,
        )

    output_field = ctx.data.get("outputField")
    if output_field:
        ctx.set_variable(str(output_field), result.get("summary"))
    return ok(ctx.node.id, result, input_value=prompt)
