"""Tests for openos_engineering plugin."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_register_wires_invoke_opencode():
    import plugins.openos_engineering as plugin

    calls: list[str] = []

    class _Ctx:
        def register_tool(self, **kw):
            calls.append(kw["name"])

        def register_hook(self, *args, **kwargs):
            calls.append(args[0] if args else kwargs.get("name", "hook"))

        def register_cli_command(self, **kw):
            calls.append(kw["name"])

    plugin.register(_Ctx())
    assert "invoke_opencode" in calls
    assert "create_subtask" in calls
    assert "openos" in calls


def test_merge_orchestrator_instructions_appends_context():
    from plugins.openos_engineering.ticket_client import merge_orchestrator_instructions

    ctx = {
        "brain_summary": "Use OAuth playbook",
        "acceptance_criteria": ["Tests pass"],
        "plan_objective": "Ship OAuth",
    }
    merged = merge_orchestrator_instructions("Base instructions", ctx)
    assert "OpenOrchestrator context" in merged
    assert "OAuth playbook" in merged
    assert merge_orchestrator_instructions(merged, ctx) == merged


def test_apply_task_context_env_sets_ticket_and_criteria(monkeypatch):
    from plugins.openos_engineering.ticket_client import apply_task_context_env

    monkeypatch.delenv("OPENTICKET_TICKET_ID", raising=False)
    monkeypatch.delenv("OPENTICKET_ACCEPTANCE_CRITERIA", raising=False)
    apply_task_context_env(
        {
            "ticket_id": "t-99",
            "acceptance_criteria": ["Done"],
            "correlation_id": "corr-x",
        }
    )
    import os

    assert os.environ["OPENTICKET_TICKET_ID"] == "t-99"
    assert "Done" in os.environ["OPENTICKET_ACCEPTANCE_CRITERIA"]


def test_build_task_prompt_minimal():
    from plugins.openos_engineering.ticket_client import build_task_prompt

    prompt = build_task_prompt({"ticket_key": "OP-1"}, "implement")
    assert "OP-1" in prompt
    assert "Implement" in prompt
    assert "Acceptance criteria" not in prompt


def test_build_ticket_prompt_alias():
    from plugins.openos_engineering.ticket_client import build_ticket_prompt

    prompt = build_ticket_prompt(
        {
            "ticket_key": "OP-1",
            "title": "Add login",
            "description": "OAuth",
            "acceptance_criteria": ["Google login works"],
        },
        "implement",
    )
    assert "OP-1" in prompt


def test_handle_invoke_opencode_missing_id():
    from plugins.openos_engineering.tools import handle_invoke_opencode

    assert "required" in handle_invoke_opencode({}).lower()


def test_get_ticket_sets_correlation_env(monkeypatch):
    from plugins.openos_engineering import ticket_client

    payload = json.dumps({"id": "t1", "correlation_id": "corr-abc"}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    monkeypatch.delenv("OPENTICKET_CORRELATION_ID", raising=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ticket = ticket_client.get_ticket("t1")
    assert ticket["correlation_id"] == "corr-abc"
    assert ticket_client.os.environ.get("OPENTICKET_CORRELATION_ID") == "corr-abc"


def test_run_opencode_headless_sets_correlation_env():
    from plugins.openos_engineering.opencode_runner import run_opencode_headless

    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = '{"type":"result","subtype":"success","result":"ok"}\n'
    proc.stderr = ""

    with patch(
        "plugins.openos_engineering.opencode_runner.resolve_opencode_binary",
        return_value=["/bin/opencode"],
    ), patch(
        "plugins.openos_engineering.opencode_runner.subprocess.run",
        return_value=proc,
    ) as run_mock:
        result = run_opencode_headless(
            "do work",
            ticket_id="t1",
            correlation_id="corr-xyz",
        )

    assert result["ok"] is True
    env = run_mock.call_args.kwargs["env"]
    assert env["OPENTICKET_CORRELATION_ID"] == "corr-xyz"
    assert env["OPENTICKET_TICKET_ID"] == "t1"
    assert env["OPENCODE_INVOKED_BY"] == "openagents"


def test_parse_stream_json_summary():
    from plugins.openos_engineering.opencode_runner import (
        extract_summary_from_stream,
        parse_stream_json_lines,
    )

    raw = '\n'.join(
        [
            '{"type":"assistant","message":{}}',
            '{"type":"result","subtype":"success","result":"Implemented feature"}',
        ]
    )
    events = parse_stream_json_lines(raw)
    assert extract_summary_from_stream(events) == "Implemented feature"


def test_handle_run_dispatch_developer():
    from plugins.openos_engineering.cli import _dispatch_run

    with patch(
        "plugins.openos_engineering.cli.handle_invoke_opencode",
        return_value="ok",
    ) as invoke:
        out = _dispatch_run(
            {
                "agent_profile": "developer",
                "task_context": {"ticket_id": "t1", "correlation_id": "c1"},
            }
        )
    assert out == "ok"
    invoke.assert_called_once()
    assert invoke.call_args.args[0]["mode"] == "implement"


def test_init_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    from plugins.openos_engineering.profiles import init_profiles

    names = init_profiles(tmp_path)
    assert set(names) == {
        "planner",
        "skill_author",
        "product_owner",
        "developer",
        "qa",
    }
    planner_cfg = (tmp_path / "profiles" / "planner" / "config.yaml").read_text()
    assert "openorchestrator:" in planner_cfg
    assert "openticket:" in planner_cfg
    assert (tmp_path / "profiles" / "developer" / "SOUL.md").is_file()
