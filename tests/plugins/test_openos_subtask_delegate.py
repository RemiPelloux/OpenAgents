"""Tests for delegate_task → OpenTicket subtask spawn."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from plugins.openos_engineering import subtask_delegate
from plugins.openos_engineering.ticket_client import resolve_ticket_id


def test_maybe_spawn_delegate_subtask_creates_ticket(monkeypatch):
    monkeypatch.setenv("OPENTICKET_TICKET_ID", "parent-1")
    monkeypatch.setenv("OPENOS_DELEGATE_SPAWN_SUBTASK", "1")

    parent_payload = json.dumps({
        "id": "parent-1",
        "correlation_id": "corr-1",
        "ticket_key": "OP-1",
    }).encode()
    subtask_payload = json.dumps({
        "id": "child-1",
        "ticket_key": "OP-2",
        "correlation_id": "corr-1",
    }).encode()

    responses = [parent_payload, subtask_payload]
    mock_resp = MagicMock()
    mock_resp.read.side_effect = responses
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = subtask_delegate.maybe_spawn_delegate_subtask(
            parent_session_id="sess-parent",
            child_session_id="sess-child",
            child_goal="Implement auth module",
        )

    assert result is not None
    assert result["id"] == "child-1"
    assert resolve_ticket_id(session_id="sess-child") == "child-1"
    assert resolve_ticket_id(session_id="sess-parent") == "parent-1"


def test_maybe_spawn_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("OPENTICKET_TICKET_ID", "parent-1")
    monkeypatch.setenv("OPENOS_DELEGATE_SPAWN_SUBTASK", "0")

    assert (
        subtask_delegate.maybe_spawn_delegate_subtask(
            parent_session_id="sess-parent",
            child_session_id="sess-child",
            child_goal="noop",
        )
        is None
    )
