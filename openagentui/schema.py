"""Workflow graph data model for OpenAgentUI.

Mirrors the node/edge JSON shape used by the (vendored, rebranded)
``apps/openagentui`` frontend so workflows exported from the visual builder
round-trip through this schema without transformation. ``NodeData`` in the
upstream TypeScript source has ~50 loosely-typed optional fields depending on
node type — rather than mirroring every field as a dataclass attribute, node
``data`` is kept as a plain dict and each node executor (see ``nodes/``)
reads only the keys it needs. This keeps the schema stable as new node
options are added in the UI without touching this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Node types supported by the schema. ``arcade`` and ``guardrails`` are kept
# for import/export compatibility with upstream templates but are not
# executable in OpenAgentUI v1 (see nodes/unsupported_node.py).
NODE_TYPES = (
    "start",
    "agent",
    "mcp",
    "transform",
    "if-else",
    "while",
    "user-approval",
    "set-state",
    "http",
    "note",
    "end",
    "arcade",
    "guardrails",
)

EXECUTION_STATUSES = ("running", "completed", "failed", "paused", "waiting-approval")
NODE_RESULT_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "pending-approval",
    "skipped",
)


@dataclass
class WorkflowNode:
    id: str
    type: str
    position: Dict[str, float] = field(default_factory=lambda: {"x": 0, "y": 0})
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WorkflowNode":
        return cls(
            id=str(raw["id"]),
            type=str(raw.get("type", "note")),
            position=dict(raw.get("position") or {"x": 0, "y": 0}),
            data=dict(raw.get("data") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "position": self.position, "data": self.data}


@dataclass
class WorkflowEdge:
    id: str
    source: str
    target: str
    source_handle: Optional[str] = None
    label: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WorkflowEdge":
        return cls(
            id=str(raw.get("id") or f"{raw['source']}-{raw['target']}"),
            source=str(raw["source"]),
            target=str(raw["target"]),
            source_handle=raw.get("sourceHandle"),
            label=raw.get("label"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "source": self.source, "target": self.target}
        if self.source_handle is not None:
            out["sourceHandle"] = self.source_handle
        if self.label is not None:
            out["label"] = self.label
        return out


@dataclass
class Workflow:
    id: str
    name: str
    description: str = ""
    category: str = ""
    tags: List[str] = field(default_factory=list)
    nodes: List[WorkflowNode] = field(default_factory=list)
    edges: List[WorkflowEdge] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    is_template: bool = False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Workflow":
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name", "Untitled workflow")),
            description=str(raw.get("description", "")),
            category=str(raw.get("category", "")),
            tags=list(raw.get("tags") or []),
            nodes=[WorkflowNode.from_dict(n) for n in raw.get("nodes") or []],
            edges=[WorkflowEdge.from_dict(e) for e in raw.get("edges") or []],
            created_at=str(raw.get("createdAt", "")),
            updated_at=str(raw.get("updatedAt", "")),
            is_template=bool(raw.get("isTemplate", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "isTemplate": self.is_template,
        }

    def node_by_id(self, node_id: str) -> Optional[WorkflowNode]:
        return next((n for n in self.nodes if n.id == node_id), None)

    def outgoing_edges(self, node_id: str) -> List[WorkflowEdge]:
        return [e for e in self.edges if e.source == node_id]


@dataclass
class NodeExecutionResult:
    node_id: str
    status: str = "pending"
    input: Any = None
    output: Any = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
        }


@dataclass
class PendingApproval:
    approval_id: str
    execution_id: str
    node_id: str
    message: str
    status: str = "pending"  # pending | approved | rejected
    created_at: str = ""
    responded_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "executionId": self.execution_id,
            "nodeId": self.node_id,
            "message": self.message,
            "status": self.status,
            "createdAt": self.created_at,
            "respondedAt": self.responded_at,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "PendingApproval":
        return cls(
            approval_id=str(raw["approvalId"]),
            execution_id=str(raw["executionId"]),
            node_id=str(raw["nodeId"]),
            message=str(raw.get("message", "")),
            status=str(raw.get("status", "pending")),
            created_at=str(raw.get("createdAt", "")),
            responded_at=raw.get("respondedAt"),
        )


@dataclass
class WorkflowExecution:
    id: str
    workflow_id: str
    status: str = "running"
    current_node_id: Optional[str] = None
    node_results: Dict[str, NodeExecutionResult] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    completed_at: Optional[str] = None
    error: Optional[str] = None
    pending_approval_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workflowId": self.workflow_id,
            "status": self.status,
            "currentNodeId": self.current_node_id,
            "nodeResults": {k: v.to_dict() for k, v in self.node_results.items()},
            "variables": self.variables,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "error": self.error,
            "pendingApprovalId": self.pending_approval_id,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WorkflowExecution":
        results = {}
        for node_id, r in (raw.get("nodeResults") or {}).items():
            results[node_id] = NodeExecutionResult(
                node_id=node_id,
                status=r.get("status", "pending"),
                input=r.get("input"),
                output=r.get("output"),
                error=r.get("error"),
                started_at=r.get("startedAt"),
                completed_at=r.get("completedAt"),
            )
        return cls(
            id=str(raw["id"]),
            workflow_id=str(raw["workflowId"]),
            status=str(raw.get("status", "running")),
            current_node_id=raw.get("currentNodeId"),
            node_results=results,
            variables=dict(raw.get("variables") or {}),
            started_at=str(raw.get("startedAt", "")),
            completed_at=raw.get("completedAt"),
            error=raw.get("error"),
            pending_approval_id=raw.get("pendingApprovalId"),
        )
