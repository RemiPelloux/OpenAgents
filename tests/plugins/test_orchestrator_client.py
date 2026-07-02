"""Tests for OpenOrchestrator task outcome callback client."""

from __future__ import annotations

from unittest.mock import patch

from plugins.openos_engineering.orchestrator_client import notify_task_outcome


@patch("plugins.openos_engineering.orchestrator_client.urllib.request.urlopen")
@patch("plugins.openos_engineering.orchestrator_client.wrap_signed_hop")
def test_notify_complete_posts_signed_envelope(wrap_mock, urlopen_mock):
    wrap_mock.return_value = {"contract_id": "CC-ORCH-003", "payload": {"reason": "ok"}}
    urlopen_mock.return_value.__enter__.return_value.status = 200

    notify_task_outcome(
        task_id="task-1",
        correlation_id="corr-1",
        success=True,
        reason="done",
        cost_usd=1.5,
        latency_ms=800,
    )

    wrap_mock.assert_called_once()
    assert wrap_mock.call_args.kwargs["contract_id"] == "CC-ORCH-003"
    assert wrap_mock.call_args.kwargs["goal_met"] is True

    req = urlopen_mock.call_args[0][0]
    assert req.full_url.endswith("/v1/tasks/task-1/complete")
    assert req.get_header("X-correlation-id") == "corr-1"


@patch("plugins.openos_engineering.orchestrator_client.urllib.request.urlopen")
@patch("plugins.openos_engineering.orchestrator_client.wrap_signed_hop")
def test_notify_skipped_when_disabled(wrap_mock, urlopen_mock):
    with patch.dict("os.environ", {"ORCHESTRATOR_CALLBACKS_ENABLED": "0"}):
        notify_task_outcome(
            task_id="task-1",
            correlation_id="corr-1",
            success=False,
            reason="fail",
        )
    wrap_mock.assert_not_called()
    urlopen_mock.assert_not_called()
