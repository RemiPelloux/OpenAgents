"""Pre-run workflow graph validation."""

from __future__ import annotations

from typing import List

from openagentui.schema import NODE_TYPES, Workflow


def validate_workflow(workflow: Workflow) -> List[str]:
    """Return human-readable errors; empty list means runnable."""
    errors: List[str] = []
    if not workflow.nodes:
        errors.append("workflow has no nodes")
        return errors

    node_ids = {n.id for n in workflow.nodes}
    if len(node_ids) != len(workflow.nodes):
        errors.append("duplicate node ids detected")

    starts = [n for n in workflow.nodes if n.type == "start"]
    if len(starts) == 0:
        errors.append("missing a start node")
    elif len(starts) > 1:
        errors.append("workflow must have exactly one start node")

    for edge in workflow.edges:
        if edge.source not in node_ids:
            errors.append(f"edge {edge.id!r} references unknown source {edge.source!r}")
        if edge.target not in node_ids:
            errors.append(f"edge {edge.id!r} references unknown target {edge.target!r}")

    for node in workflow.nodes:
        if node.type not in NODE_TYPES:
            errors.append(f"node {node.id}: unsupported type {node.type!r}")
            continue
        data = node.data or {}
        if node.type == "agent" and not (data.get("instructions") or data.get("systemPrompt")):
            errors.append(f"node {node.id}: agent node needs instructions")
        if node.type == "mcp" and not (data.get("mcpTool") or data.get("mcpAction")):
            errors.append(f"node {node.id}: tool node needs mcpTool")
        if node.type in ("if-else", "while") and not data.get("condition"):
            errors.append(f"node {node.id}: {node.type} node needs condition")
        if node.type == "codex" and not (data.get("prompt") or data.get("instructions")):
            errors.append(f"node {node.id}: codex node needs prompt")
        if node.type == "workflow" and not data.get("subWorkflowId"):
            errors.append(f"node {node.id}: sub-workflow node needs subWorkflowId")
        if node.type in ("if-else", "while", "user-approval"):
            handles = {e.source_handle for e in workflow.outgoing_edges(node.id) if e.source_handle}
            if node.type == "if-else" and not {"true", "false"} & {h.lower() for h in handles if h}:
                errors.append(f"node {node.id}: if-else needs true/false outgoing edges")

    return errors
