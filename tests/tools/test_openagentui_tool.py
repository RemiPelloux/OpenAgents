"""Agent-callable OpenAgentUI tool handlers — create/list/run/ensure via YAML."""

from __future__ import annotations

import json

from openagentui import store
from openagentui.schema import Workflow
from tools.openagentui_tool import (
    handle_create_openagentui_workflow_from_yaml,
    handle_ensure_openagentui_workflow,
    handle_export_openagentui_workflow_yaml,
    handle_list_openagentui_workflows,
    handle_run_openagentui_workflow,
)

SAMPLE_YAML = """
id: wf_tool_test
name: Tool Test Flow
nodes:
  - id: start
    type: start
    data: {}
  - id: end
    type: end
    data: {}
edges:
  - id: e1
    source: start
    target: end
"""


def _parse_result(raw: str) -> dict:
    return json.loads(raw)


def test_list_openagentui_workflows_empty():
    payload = _parse_result(handle_list_openagentui_workflows({}))
    assert payload["workflows"] == []


def test_create_and_export_yaml_roundtrip():
    created = _parse_result(
        handle_create_openagentui_workflow_from_yaml({"yaml": SAMPLE_YAML})
    )
    assert created["created"] is True
    assert created["workflow"]["id"] == "wf_tool_test"

    exported = _parse_result(
        handle_export_openagentui_workflow_yaml({"workflow": "Tool Test Flow"})
    )
    assert exported["id"] == "wf_tool_test"
    assert "Tool Test Flow" in exported["yaml"]


def test_ensure_creates_once():
    first = _parse_result(
        handle_ensure_openagentui_workflow({"name": "Ensure Me", "yaml": SAMPLE_YAML})
    )
    assert first["created"] is True

    second = _parse_result(
        handle_ensure_openagentui_workflow({"name": "Ensure Me", "yaml": SAMPLE_YAML})
    )
    assert second["created"] is False
    assert second["workflow"]["id"] == "wf_tool_test"


def test_run_openagentui_workflow_completes():
    store.save_workflow(
        Workflow.from_dict({
            "id": "wf_run_tool",
            "name": "Runnable",
            "nodes": [
                {"id": "start", "type": "start", "data": {}},
                {"id": "end", "type": "end", "data": {}},
            ],
            "edges": [{"id": "e1", "source": "start", "target": "end"}],
        })
    )
    result = _parse_result(handle_run_openagentui_workflow({"workflow": "Runnable"}))
    assert result["status"] == "completed"
    assert result["workflowName"] == "Runnable"


def test_create_yaml_requires_content():
    raw = handle_create_openagentui_workflow_from_yaml({})
    payload = _parse_result(raw)
    assert "yaml" in payload["error"].lower()
