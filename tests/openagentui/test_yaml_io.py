"""Tests for openagentui.yaml_io."""

from openagentui.schema import Workflow
from openagentui.yaml_io import (
    validate_workflow_yaml,
    workflow_from_yaml,
    workflow_to_yaml_text,
)

SAMPLE_YAML = """
id: wf_yaml_test
name: YAML Test Flow
description: headless sample
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


def test_workflow_yaml_roundtrip():
    workflow = workflow_from_yaml(SAMPLE_YAML)
    assert workflow.id == "wf_yaml_test"
    assert workflow.name == "YAML Test Flow"
    assert len(workflow.nodes) == 2

    text = workflow_to_yaml_text(workflow)
    again = workflow_from_yaml(text)
    assert again.id == workflow.id
    assert again.name == workflow.name


def test_validate_workflow_yaml_ok():
    ok, err = validate_workflow_yaml(SAMPLE_YAML)
    assert ok is True
    assert err == ""


def test_validate_workflow_yaml_bad():
    ok, err = validate_workflow_yaml("not: [valid")
    assert ok is False
    assert err


def test_workflow_id_override():
    workflow = workflow_from_yaml(SAMPLE_YAML, workflow_id="wf_custom")
    assert workflow.id == "wf_custom"
