"""Tests for ticket DoD loop helpers."""

from __future__ import annotations

from unittest.mock import patch

from plugins.openos_engineering.ticket_dod_loop import (
    dev_phase_complete,
    is_ticket_dod,
    opencode_mode_for_profile,
    qa_phase_complete,
    run_ticket_dod_loop,
)


def test_is_ticket_dod():
    assert is_ticket_dod({"status": "done"})
    assert not is_ticket_dod({"status": "in_review"})


def test_dev_phase_complete():
    assert dev_phase_complete({"status": "in_review"})
    assert not dev_phase_complete({"status": "in_progress"})


def test_opencode_mode_for_developer():
    assert (
        opencode_mode_for_profile("developer", {"status": "in_progress"}) == "implement"
    )
    assert opencode_mode_for_profile("developer", {"status": "in_review"}) is None


def test_opencode_mode_for_qa():
    assert opencode_mode_for_profile("qa", {"status": "in_review"}) == "review"
    assert opencode_mode_for_profile("qa", {"status": "done"}) is None


def test_run_ticket_dod_loop_stops_at_in_review():
    tickets = [{"id": "t1", "ticket_key": "OP-1", "status": "in_progress"}]

    def fake_get(_tid):
        return tickets[-1]

    calls = []

    def fake_invoke(**kwargs):
        calls.append(kwargs["mode"])
        tickets.append({"id": "t1", "ticket_key": "OP-1", "status": "in_review"})
        return {"ok": True, "summary": "implemented"}

    with patch(
        "plugins.openos_engineering.ticket_dod_loop.get_ticket", side_effect=fake_get
    ):
        result = run_ticket_dod_loop(
            "t1",
            profile="developer",
            invoke_once=fake_invoke,
        )

    assert result["ok"] is True
    assert result["ticket_status"] == "in_review"
    assert calls == ["implement"]


def test_handle_run_dispatch_dod_loop_flag():
    from plugins.openos_engineering.cli import _dispatch_run

    with patch(
        "plugins.openos_engineering.cli.handle_run_ticket_dod_loop",
        return_value="loop ok",
    ) as dod_loop:
        out = _dispatch_run({
            "agent_profile": "developer",
            "task_context": {"ticket_id": "t1", "loop_until_dod": True},
        })
    assert out == "loop ok"
    dod_loop.assert_called_once()
