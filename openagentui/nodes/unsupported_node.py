"""``arcade`` / ``guardrails`` nodes — schema-compatible, not executable.

Both upstream node types depend on paid external SaaS (Arcade.dev tool
auth, a moderation API) that OpenAgentUI does not add. Workflows imported
from upstream templates keep these nodes on the canvas (so the graph still
renders and can be edited/rewired) but running them fails with a clear,
actionable error instead of crashing the engine.
"""

from __future__ import annotations

from openagentui.nodes.base import NodeContext, failed
from openagentui.schema import NodeExecutionResult


def execute(ctx: NodeContext) -> NodeExecutionResult:
    return failed(
        ctx.node.id,
        f"node type '{ctx.node.type}' is not supported in OpenAgentUI (requires external SaaS "
        "not integrated in this fork). Replace it with an 'agent' or 'mcp' node.",
    )
