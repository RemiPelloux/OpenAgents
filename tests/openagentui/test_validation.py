"""Tests for openagentui.validation."""

from openagentui.schema import Workflow
from openagentui.validation import validate_workflow


def _wf(**kwargs):
    base = {
        "id": "wf1",
        "name": "Test",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }
    base.update(kwargs)
    return Workflow.from_dict(base)


def test_valid_linear_workflow():
    assert validate_workflow(_wf()) == []


def test_missing_start():
    wf = _wf(
        nodes=[{"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}}]
    )
    assert any("start" in err for err in validate_workflow(wf))


def test_agent_without_instructions():
    wf = _wf(
        nodes=[
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "a1", "type": "agent", "position": {"x": 0, "y": 0}, "data": {}},
        ]
    )
    assert any("instructions" in err for err in validate_workflow(wf))
