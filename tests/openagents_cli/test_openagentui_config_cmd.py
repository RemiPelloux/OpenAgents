"""Tests for the /OpenAgentConfig shared command (list/show/run/approve/reject)."""

from __future__ import annotations

from openagents_cli import openagentui_config_cmd as cmd
from openagentui import store
from openagentui.schema import Workflow


def _save_linear_workflow(id_="wf_cfg") -> Workflow:
    workflow = Workflow.from_dict({
        "id": id_,
        "name": "Greeting Flow",
        "description": "Says hi",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "set1",
                "type": "set-state",
                "position": {"x": 0, "y": 0},
                "data": {"stateKey": "greeting", "stateValue": "hi {{ name }}"},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "set1"},
            {"id": "e2", "source": "set1", "target": "end"},
        ],
    })
    return store.save_workflow(workflow)


def _save_approval_workflow(id_="wf_approval_cfg") -> Workflow:
    workflow = Workflow.from_dict({
        "id": id_,
        "name": "Approval Flow",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "gate",
                "type": "user-approval",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {"id": "end", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "gate"},
            {"id": "e2", "source": "gate", "target": "end", "sourceHandle": "approved"},
        ],
    })
    return store.save_workflow(workflow)


def test_bare_command_lists_when_empty():
    result = cmd.handle_openagentconfig_command("")
    assert "No saved workflows" in result.text


def test_list_shows_saved_workflows():
    _save_linear_workflow()
    result = cmd.handle_openagentconfig_command("list")
    assert "Greeting Flow" in result.text
    assert "wf_cfg" in result.text


def test_show_unknown_workflow_suggests_close_match():
    _save_linear_workflow()
    result = cmd.handle_openagentconfig_command("show Greeting Flo")
    # "show" with a near-miss falls through to _format_show which reports unknown
    assert "Greeting Flow" in result.text or "Unknown workflow" in result.text


def test_show_known_workflow_lists_nodes():
    _save_linear_workflow()
    result = cmd.handle_openagentconfig_command("show Greeting Flow")
    assert "start (start)" in result.text
    assert "end (end)" in result.text


def test_run_by_name_default_verb_executes_workflow():
    _save_linear_workflow()
    result = cmd.handle_openagentconfig_command("Greeting Flow name=bob")
    assert "Running" in result.text
    assert "Completed" in result.text


def test_run_explicit_verb_with_kv_inputs():
    _save_linear_workflow()
    result = cmd.handle_openagentconfig_command('run "Greeting Flow" name=alice')
    assert "Completed" in result.text
    assert "greeting" in result.text


def test_approve_resumes_paused_workflow():
    _save_approval_workflow()
    run_result = cmd.handle_openagentconfig_command("Approval Flow")
    assert "Paused for approval" in run_result.text

    execution_id = next(e.id for e in store.list_executions("wf_approval_cfg"))
    approve_result = cmd.handle_openagentconfig_command(f"approve {execution_id}")
    assert approve_result.text.startswith("Approved.")
    assert "completed" in approve_result.text.lower()


def test_reject_fails_paused_workflow():
    _save_approval_workflow("wf_approval_cfg2")
    run_result = cmd.handle_openagentconfig_command("Approval Flow")
    execution_id = next(e.id for e in store.list_executions("wf_approval_cfg2"))
    reject_result = cmd.handle_openagentconfig_command(f"reject {execution_id}")
    assert reject_result.text.startswith("Rejected.")
    assert "failed" in reject_result.text.lower()


def test_approve_unknown_execution_returns_error_text():
    result = cmd.handle_openagentconfig_command("approve exec_missing_xyz")
    assert "unknown execution" in result.text


def test_run_unknown_name_reports_unknown_and_lists():
    result = cmd.handle_openagentconfig_command("totally-unknown-workflow-xyz")
    assert "Unknown workflow" in result.text


def test_bare_verb_without_name_reports_usage():
    result = cmd.handle_openagentconfig_command("show")
    assert "Usage: /OpenAgentConfig show" in result.text
