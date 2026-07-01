"""Graph walker for OpenAgentUI workflows.

Walks ``Workflow.nodes``/``edges`` starting at the ``start`` node (or the
paused node, on resume), dispatching each node to its executor in
``nodes.NODE_EXECUTORS`` and following the outgoing edge selected by the
node's result (a plain "next edge" for most types, a ``branch`` value for
``if-else``/``while``/``user-approval``). Progress is persisted to disk
after every node so a crash mid-run leaves a resumable, inspectable
execution record rather than losing all progress.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from openagentui import store
from openagentui.nodes import NODE_EXECUTORS, NodeContext
from openagentui.schema import NodeExecutionResult, Workflow, WorkflowExecution
from openagentui.tool_catalog import ensure_tools_loaded

logger = logging.getLogger(__name__)

MAX_STEPS = 1000
NodeCallback = Callable[[NodeExecutionResult], None]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_execution(workflow: Workflow, inputs: Optional[Dict[str, Any]] = None) -> WorkflowExecution:
    return WorkflowExecution(
        id=store.new_id("exec"),
        workflow_id=workflow.id,
        status="running",
        variables=dict(inputs or {}),
        started_at=_now_iso(),
    )


class WorkflowEngine:
    """Runs (or resumes) a single ``WorkflowExecution`` against its ``Workflow``."""

    def __init__(self, workflow: Workflow, execution: WorkflowExecution):
        self.workflow = workflow
        self.execution = execution

    def _entry_node_id(self) -> Optional[str]:
        start = next((n for n in self.workflow.nodes if n.type == "start"), None)
        if start:
            return start.id
        targets = {e.target for e in self.workflow.edges}
        entry = next((n for n in self.workflow.nodes if n.id not in targets), None)
        return entry.id if entry else None

    def _next_node_id(self, node_id: str, result: NodeExecutionResult) -> Optional[str]:
        edges = self.workflow.outgoing_edges(node_id)
        if not edges:
            return None
        branch = result.output.get("branch") if isinstance(result.output, dict) else None
        if branch is None:
            return edges[0].target
        for edge in edges:
            if (edge.source_handle or "").lower() == str(branch).lower():
                return edge.target
        logger.warning("openagentui: node %s branch %r has no matching outgoing edge", node_id, branch)
        return None

    def run(self, on_node: Optional[NodeCallback] = None) -> WorkflowExecution:
        ensure_tools_loaded()
        execution = self.execution
        current_id = execution.current_node_id or self._entry_node_id()
        if current_id is None:
            execution.status = "failed"
            execution.error = "workflow has no start node"
            store.save_execution(execution)
            return execution

        steps = 0
        while current_id is not None:
            steps += 1
            if steps > MAX_STEPS:
                execution.status = "failed"
                execution.error = f"exceeded {MAX_STEPS} steps (possible cycle without exit condition)"
                break

            node = self.workflow.node_by_id(current_id)
            if node is None:
                execution.status = "failed"
                execution.error = f"workflow references missing node: {current_id}"
                break

            executor = NODE_EXECUTORS.get(node.type)
            if executor is None:
                execution.status = "failed"
                execution.error = f"no executor registered for node type: {node.type}"
                break

            execution.current_node_id = node.id
            try:
                result = executor(NodeContext(node=node, execution=execution))
            except Exception as exc:  # defensive: a node executor must never crash the run
                logger.exception("openagentui: unhandled error in node %s", node.id)
                result = NodeExecutionResult(node_id=node.id, status="failed", error=str(exc))

            execution.node_results[node.id] = result
            store.save_execution(execution)
            if on_node:
                on_node(result)

            if result.status == "failed":
                execution.status = "failed"
                execution.error = result.error
                break
            if result.status == "pending-approval":
                execution.status = "waiting-approval"
                break
            if node.type == "end":
                execution.status = "completed"
                execution.completed_at = _now_iso()
                break

            current_id = self._next_node_id(node.id, result)
            if current_id is None:
                execution.status = "completed"
                execution.completed_at = _now_iso()
                break

        store.save_execution(execution)
        return execution


def run_workflow(
    workflow: Workflow,
    inputs: Optional[Dict[str, Any]] = None,
    on_node: Optional[NodeCallback] = None,
) -> WorkflowExecution:
    """Start a brand-new execution of ``workflow`` and run it to completion/pause."""
    execution = new_execution(workflow, inputs)
    store.save_execution(execution)
    return WorkflowEngine(workflow, execution).run(on_node=on_node)


def resume_execution(execution_id: str, on_node: Optional[NodeCallback] = None) -> WorkflowExecution:
    """Continue a paused (``waiting-approval``) execution from where it stopped."""
    execution = store.get_execution(execution_id)
    if execution is None:
        raise ValueError(f"unknown execution: {execution_id}")
    workflow = store.get_workflow(execution.workflow_id)
    if workflow is None:
        raise ValueError(f"execution {execution_id} references missing workflow {execution.workflow_id}")
    execution.status = "running"
    return WorkflowEngine(workflow, execution).run(on_node=on_node)
