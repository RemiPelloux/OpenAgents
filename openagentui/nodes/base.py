"""Shared context/result helpers for node executors."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openagentui.schema import NodeExecutionResult, WorkflowExecution, WorkflowNode


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class NodeContext:
    """Everything a node executor needs: the node itself, and live run state."""

    node: WorkflowNode
    execution: WorkflowExecution

    @property
    def data(self) -> Dict[str, Any]:
        return self.node.data

    def rendered(self, key: str, default: Any = "") -> Any:
        """Read ``node.data[key]`` with ``{{ }}`` placeholders substituted."""
        from openagentui.templating import render

        return render(
            self.data.get(key, default),
            variables=self.execution.variables,
            node_results=self.execution.node_results,
        )

    def set_variable(self, key: str, value: Any) -> None:
        self.execution.variables[key] = value


def start_result(node_id: str, input_value: Any = None) -> NodeExecutionResult:
    return NodeExecutionResult(node_id=node_id, status="running", input=input_value, started_at=_now_iso())


def ok(node_id: str, output: Any, *, input_value: Any = None) -> NodeExecutionResult:
    now = _now_iso()
    return NodeExecutionResult(
        node_id=node_id, status="completed", input=input_value, output=output,
        started_at=now, completed_at=now,
    )


def failed(node_id: str, error: str, *, input_value: Any = None) -> NodeExecutionResult:
    now = _now_iso()
    return NodeExecutionResult(
        node_id=node_id, status="failed", input=input_value, error=error,
        started_at=now, completed_at=now,
    )


def pending_approval(node_id: str, *, input_value: Any = None) -> NodeExecutionResult:
    now = _now_iso()
    return NodeExecutionResult(
        node_id=node_id, status="pending-approval", input=input_value, started_at=now,
    )
