"""Tests for Brain observation client — CC-BRAIN-001."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from plugins.openos_engineering.brain_client import (
    build_agent_run_observation,
    emit_agent_run_observation,
    ingest_observation,
)


def test_build_agent_run_observation_completed():
    body = build_agent_run_observation(
        "agent.run.completed",
        ticket_id="t1",
        ticket_key="OP-1",
        mode="implement",
        agent_profile="developer",
        agent_run_id="run-1",
        correlation_id="corr-abc",
        summary="Shipped feature",
    )
    assert body["app"] == "openagents"
    assert body["domain"] == "openos"
    assert "OP-1" in body["title"]
    assert "Shipped feature" in body["content"]
    assert "corr-abc" in body["content"]


def test_build_agent_run_observation_failed_includes_exit_code():
    body = build_agent_run_observation(
        "agent.run.failed",
        ticket_id="t1",
        mode="review",
        agent_profile="qa",
        agent_run_id="run-2",
        exit_code=1,
    )
    assert "exit_code=1" in body["content"]
    assert "failed" in body["title"].lower()


def test_ingest_observation_no_ops_without_env(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_URL", raising=False)
    monkeypatch.delenv("OPENBRAIN_API_URL", raising=False)
    with patch("urllib.request.urlopen") as urlopen:
        ingest_observation({"app": "openagents", "title": "x", "content": "y"})
    urlopen.assert_not_called()


def test_ingest_observation_posts_when_configured(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_URL", "http://localhost:3001")
    monkeypatch.setenv("OPENBRAIN_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status = 202
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp) as urlopen:
        ingest_observation({
            "observationId": "openagents:test:1",
            "app": "openagents",
            "sourceType": "event",
            "title": "Run started",
            "content": "mode=implement",
            "domain": "openos",
        })

    req = urlopen.call_args[0][0]
    assert req.full_url == "http://localhost:3001/api/v1/brain/observations"
    assert req.get_header("Authorization") == "Bearer test-key"
    payload = json.loads(req.data.decode())
    assert payload["app"] == "openagents"


def test_emit_agent_run_observation_builds_and_sends(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_URL", "http://localhost:3001")
    monkeypatch.setenv("AXON_AGENT_API_KEY", "axon-key")
    with patch("plugins.openos_engineering.brain_client.ingest_observation") as ingest:
        emit_agent_run_observation(
            "agent.run.started",
            ticket_id="t1",
            ticket_key="OP-9",
            mode="implement",
            agent_profile="developer",
            agent_run_id="run-9",
            correlation_id="corr-9",
        )
    body = ingest.call_args[0][0]
    assert body["observationId"] == "openagents:agent.run.started:run-9"
    assert "OP-9" in body["title"]
