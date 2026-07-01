"""Per-node-type executors for the OpenAgentUI workflow engine.

Each module exposes an ``execute(ctx) -> NodeExecutionResult`` function.
``NODE_EXECUTORS`` maps a ``WorkflowNode.type`` to its executor — this is
the single dispatch table ``engine.py`` walks against.
"""

from __future__ import annotations

from typing import Callable, Dict

from openagentui.nodes.base import NodeContext
from openagentui.nodes.agent_node import execute as execute_agent
from openagentui.nodes.approval_node import execute as execute_approval
from openagentui.nodes.control_flow import execute_if_else, execute_while
from openagentui.nodes.http_node import execute as execute_http
from openagentui.nodes.simple_nodes import (
    execute_end,
    execute_note,
    execute_set_state,
    execute_start,
)
from openagentui.nodes.tool_node import execute as execute_tool
from openagentui.nodes.transform_node import execute as execute_transform
from openagentui.nodes.unsupported_node import execute as execute_unsupported
from openagentui.schema import NodeExecutionResult

NODE_EXECUTORS: Dict[str, Callable[[NodeContext], NodeExecutionResult]] = {
    "start": execute_start,
    "agent": execute_agent,
    "mcp": execute_tool,
    "transform": execute_transform,
    "if-else": execute_if_else,
    "while": execute_while,
    "user-approval": execute_approval,
    "set-state": execute_set_state,
    "http": execute_http,
    "note": execute_note,
    "end": execute_end,
    "arcade": execute_unsupported,
    "guardrails": execute_unsupported,
}

__all__ = ["NODE_EXECUTORS", "NodeContext"]
