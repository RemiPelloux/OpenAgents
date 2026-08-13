"""Tests for openos_engineering plugin."""

from __future__ import annotations

import json
import base64
import subprocess
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
    assert "OpenProtocol CODER" in prompt
    assert "agent/OP-1/" in prompt
    assert "GITHUB_TOKEN" in prompt


def test_build_task_prompt_review_omits_openprotocol():
    from plugins.openos_engineering.ticket_client import build_task_prompt

    prompt = build_task_prompt({"ticket_key": "OP-1"}, "review")
    assert "OpenProtocol" not in prompt


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

    assert "required" in handle_invoke_opencode({}, task_id="registry-task").lower()


def test_invoke_opencode_advances_backlog_through_implementation(monkeypatch):
    from plugins.openos_engineering import tools

    transitions = []
    monkeypatch.setattr(
        tools,
        "get_ticket",
        lambda _ticket_id: {
            "id": "ticket-1",
            "ticket_key": "OB-42",
            "status": "backlog",
            "correlation_id": "corr-1",
        },
    )

    def transition(_ticket_id, to_status, **kwargs):
        transitions.append((to_status, kwargs["actor_profile"]))
        return {"id": "ticket-1", "status": to_status, "correlation_id": "corr-1"}

    monkeypatch.setattr(tools, "update_ticket_status", transition)
    monkeypatch.setattr(tools, "build_task_prompt", lambda *_args: "prompt")
    monkeypatch.setattr(tools, "run_opencode_headless", lambda *_args, **_kwargs: _successful_opencode_result())
    monkeypatch.setattr(tools, "emit_rec_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "emit_agent_run_observation", lambda *_args, **_kwargs: None)

    result = tools.invoke_opencode_once(ticket_id="ticket-1")

    assert result["ok"] is True
    assert transitions == [("todo", "product_owner"), ("in_progress", "developer")]


def test_invoke_opencode_marks_passing_review_done(monkeypatch):
    from plugins.openos_engineering import tools

    statuses = iter(["in_review", "qa"])
    transitions = []
    monkeypatch.setattr(
        tools,
        "get_ticket",
        lambda _ticket_id: {
            "id": "ticket-1",
            "ticket_key": "OB-42",
            "status": next(statuses),
            "correlation_id": "corr-1",
        },
    )

    def transition(_ticket_id, to_status, **kwargs):
        transitions.append((to_status, kwargs["actor_profile"]))
        return {"id": "ticket-1", "status": to_status, "correlation_id": "corr-1"}

    monkeypatch.setattr(tools, "update_ticket_status", transition)
    monkeypatch.setattr(tools, "build_task_prompt", lambda *_args: "prompt")
    monkeypatch.setattr(tools, "run_opencode_headless", lambda *_args, **_kwargs: _successful_opencode_result())
    monkeypatch.setattr(tools, "emit_rec_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools, "emit_agent_run_observation", lambda *_args, **_kwargs: None)

    result = tools.invoke_opencode_once(ticket_id="ticket-1", mode="review")

    assert result["ok"] is True
    assert transitions == [("qa", "qa"), ("done", "qa")]


def _successful_opencode_result():
    return {
        "ok": True,
        "summary": "tests passed",
        "stderr": "",
        "exit_code": 0,
        "files_edited": ["hello.py"],
        "session_id": "session-1",
        "workdir": "/tmp/run",
        "branch": "agent/ticket-1",
        "commit_sha": "a" * 40,
        "git_clean": True,
    }


def test_registered_handlers_accept_registry_metadata():
    import inspect
    from plugins.openos_engineering import tools

    for name in (
        "handle_invoke_opencode",
        "handle_run_ticket_dod_loop",
        "handle_submit_ticket_result",
        "handle_create_subtask",
        "handle_create_ticket",
        "handle_set_ticket_eta",
        "handle_invoke_codex",
    ):
        signature = inspect.signature(getattr(tools, name))
        assert any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ), f"{name} must accept registry dispatch metadata"


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
    agent_call = next(call for call in run_mock.call_args_list if "env" in call.kwargs)
    env = agent_call.kwargs["env"]
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


def test_managed_workspace_rejects_path_outside_root(tmp_path, monkeypatch):
    from plugins.openos_engineering.opencode_runner import _managed_workspace

    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("OPENOS_WORKSPACE_ROOT", str(managed))

    with pytest.raises(ValueError, match="outside managed root"):
        _managed_workspace(str(outside), correlation_id="corr-1", run_id="run-1")


def test_managed_workspace_scopes_prompt_to_isolated_worktree(tmp_path, monkeypatch):
    from plugins.openos_engineering.opencode_runner import run_opencode_headless

    managed = tmp_path / "managed"
    source = managed / "hello"
    source.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "seed"], check=True)
    monkeypatch.setenv("OPENOS_WORKSPACE_ROOT", str(managed))

    proc = MagicMock(returncode=0, stdout='{"type":"result","subtype":"success","result":"ok"}\n', stderr="")
    real_run = subprocess.run
    with patch(
        "plugins.openos_engineering.opencode_runner.resolve_opencode_binary",
        return_value=["/bin/opencode"],
    ), patch(
        "plugins.openos_engineering.opencode_runner.subprocess.run",
        wraps=subprocess.run,
    ) as run_mock:
        run_mock.side_effect = lambda command, **kwargs: (
            proc if command[0] == "/bin/opencode" else real_run(command, **kwargs)
        )
        result = run_opencode_headless(
            f"Implement the ticket in {source}",
            cwd=str(source),
            correlation_id="corr-1",
            run_id="run-1",
        )

    agent_call = next(call for call in run_mock.call_args_list if call.args[0][0] == "/bin/opencode")
    scoped_prompt = agent_call.args[0][-1]
    assert str(source) not in scoped_prompt
    assert "current working directory" in scoped_prompt
    assert "--dangerously-skip-permissions" in agent_call.args[0]
    assert result["workdir"] == str(managed / "runs" / "run-1")
    assert result["branch"] == "agent/run-1"


def test_managed_task_prompt_uses_existing_agent_branch(monkeypatch):
    from plugins.openos_engineering.ticket_client import build_task_prompt

    monkeypatch.setenv("OPENOS_WORKSPACE_ROOT", "/managed")
    prompt = build_task_prompt({"ticket_key": "OB-12"}, "implement")

    assert "already created and checked out" in prompt
    assert "git fetch" not in prompt
    assert "git push" not in prompt


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
    from plugins.openos_engineering.profiles import init_profiles, list_profile_ids

    names = init_profiles(tmp_path)
    assert len(names) == 18
    assert set(names) == set(list_profile_ids())
    assert "mesh_engineer" in names
    assert "sales" in names
    planner_cfg = (tmp_path / "profiles" / "planner" / "config.yaml").read_text()
    assert "openorchestrator:" in planner_cfg
    assert "openticket:" in planner_cfg
    developer_cfg = (tmp_path / "profiles" / "developer" / "config.yaml").read_text()
    assert "terminal" not in developer_cfg
    assert "delegation" not in developer_cfg
    assert "mcp_servers:\n" in developer_cfg
    assert "  openticket:" not in developer_cfg
    assert "  - skills" in developer_cfg
    assert "openos_engineering" in developer_cfg
    sales_cfg = (tmp_path / "profiles" / "sales" / "config.yaml").read_text()
    assert "opencrm:" in sales_cfg
    intent_cfg = (tmp_path / "profiles" / "intent_classifier" / "config.yaml").read_text()
    assert "open-orchestrator-intent" in intent_cfg
    assert (tmp_path / "profiles" / "developer" / "SOUL.md").is_file()


def test_ensure_profiles_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAGENTS_HOME", str(tmp_path))
    from plugins.openos_engineering.profiles import ensure_profile, ensure_profiles

    first = ensure_profiles(["developer", "qa"], home=tmp_path)
    assert first["developer"] == "created"
    assert first["qa"] == "created"
    second = ensure_profiles(["developer"], home=tmp_path)
    assert second["developer"] == "exists"
    with pytest.raises(ValueError, match="unknown profile"):
        ensure_profile("not_a_profile", home=tmp_path)


def test_unwrap_orchestrator_run_envelope(monkeypatch):
    from plugins.openos_engineering.orchestrator_dispatch import unwrap_orchestrator_run_body

    monkeypatch.setenv("OPENCONTRACT_REQUIRE_SIGNATURE", "0")
    plain = {"agent_profile": "developer", "input": "hello", "task_context": {}}
    assert unwrap_orchestrator_run_body(plain) == plain

    wrapped = {
        "contract_id": "CC-ORCH-004",
        "producer": "OpenOrchestrator",
        "consumer": "OpenAgents",
        "payload": {"agent_profile": "developer", "input": "hello", "task_context": {}},
    }
    assert unwrap_orchestrator_run_body(wrapped)["agent_profile"] == "developer"

    openteam = {
        "contract_id": "CC-OT-001",
        "producer": "OpenTeam",
        "consumer": "OpenAgents",
        "payload": {"agent_profile": "tiktok_prospector", "input": "hello"},
    }
    assert unwrap_orchestrator_run_body(openteam)["agent_profile"] == "tiktok_prospector"


def test_unwrap_orchestrator_requires_envelope_when_strict(monkeypatch):
    from plugins.openos_engineering.orchestrator_dispatch import unwrap_orchestrator_run_body

    monkeypatch.setenv("OPENCONTRACT_REQUIRE_SIGNATURE", "1")
    plain = {"agent_profile": "developer", "input": "hello", "task_context": {}}
    try:
        unwrap_orchestrator_run_body(plain)
        assert False, "expected CONTRACT_ENVELOPE_REQUIRED"
    except ValueError as exc:
        assert "CONTRACT_ENVELOPE_REQUIRED" in str(exc)
    finally:
        monkeypatch.delenv("OPENCONTRACT_REQUIRE_SIGNATURE", raising=False)


def test_unwrap_orchestrator_verifies_unicode_signature(monkeypatch):
    from nacl.signing import SigningKey
    from plugins.openos_engineering.orchestrator_dispatch import (
        _envelope_signing_bytes,
        unwrap_orchestrator_run_body,
    )

    seed = base64.b64decode("rtXpqdgTKoi+frA2KqGLxDcD182kS6Z5UYnILHiRhoM=")
    envelope = {
        "contract_id": "CC-ORCH-004",
        "correlation_id": "00000000-0000-4000-8000-000000000099",
        "status": "success",
        "goal_met": True,
        "producer": "OpenOrchestrator",
        "consumer": "OpenAgents",
        "payload": {"input": "Implement — résumé", "agent_profile": "developer"},
        "prerequisites_ok": True,
        "timestamp": "2026-07-19T00:00:00.000Z",
    }
    signature = SigningKey(seed).sign(_envelope_signing_bytes(envelope)).signature
    envelope["signature"] = {
        "algorithm": "ed25519",
        "signer_id": "OpenOrchestrator",
        "value": base64.b64encode(signature).decode(),
    }

    monkeypatch.delenv("OPENCONTRACT_DEV_KEYS", raising=False)
    assert unwrap_orchestrator_run_body(envelope)["input"] == "Implement — résumé"


def test_unwrap_dispatch_rejects_wrong_contract_parties(monkeypatch):
    from plugins.openos_engineering.orchestrator_dispatch import unwrap_orchestrator_run_body

    monkeypatch.setenv("OPENCONTRACT_REQUIRE_SIGNATURE", "0")
    envelope = {
        "contract_id": "CC-OT-001",
        "producer": "OpenOrchestrator",
        "consumer": "OpenAgents",
        "payload": {"input": "hello"},
    }
    with pytest.raises(ValueError, match="expected producer OpenTeam"):
        unwrap_orchestrator_run_body(envelope)
