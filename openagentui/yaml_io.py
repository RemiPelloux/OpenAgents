"""YAML import/export for OpenAgentUI workflows (headless authoring)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import yaml

from openagentui.schema import Workflow


def workflow_to_yaml_dict(workflow: Workflow) -> Dict[str, Any]:
    """Serialize a workflow to a YAML-friendly dict (no React Flow positions required)."""
    nodes: List[Dict[str, Any]] = []
    for node in workflow.nodes:
        entry: Dict[str, Any] = {
            "id": node.id,
            "type": node.type,
            "data": dict(node.data or {}),
        }
        if node.position:
            entry["position"] = node.position
        nodes.append(entry)

    edges = [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            **({"sourceHandle": edge.source_handle} if edge.source_handle else {}),
            **({"label": edge.label} if edge.label else {}),
        }
        for edge in workflow.edges
    ]

    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "category": workflow.category,
        "tags": list(workflow.tags or []),
        "isTemplate": workflow.is_template,
        "nodes": nodes,
        "edges": edges,
    }


def workflow_to_yaml_text(workflow: Workflow) -> str:
    return yaml.safe_dump(
        workflow_to_yaml_dict(workflow), sort_keys=False, allow_unicode=True
    )


def parse_workflow_yaml(text: str) -> Dict[str, Any]:
    data = yaml.safe_load(text or "")
    if not isinstance(data, dict):
        raise ValueError("workflow YAML must be a mapping at the top level")
    return data


def workflow_from_yaml(text: str, *, workflow_id: str | None = None) -> Workflow:
    """Parse YAML text into a ``Workflow`` dataclass."""
    raw = parse_workflow_yaml(text)
    if workflow_id:
        raw["id"] = workflow_id
    if not raw.get("id"):
        raise ValueError("workflow YAML must include an 'id' or pass workflow_id")
    if not raw.get("name"):
        raw["name"] = str(raw["id"]).replace("_", " ").title()
    return Workflow.from_dict(raw)


def validate_workflow_yaml(text: str) -> Tuple[bool, str]:
    try:
        workflow_from_yaml(text)
        return True, ""
    except Exception as exc:
        return False, str(exc)
