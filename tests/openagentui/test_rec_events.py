"""Tests for openagentui.rec_events."""

from unittest.mock import patch

from openagentui.rec_events import emit_execution_rec_event
from openagentui.schema import Workflow, WorkflowExecution


def test_emit_completed_when_openrec_configured(monkeypatch):
    monkeypatch.setenv("OPENREC_URL", "http://127.0.0.1:8099")
    wf = Workflow.from_dict({"id": "wf1", "name": "Demo", "nodes": [], "edges": []})
    ex = WorkflowExecution(
        id="exec1", workflow_id="wf1", status="completed", started_at="now"
    )
    with patch("openagentui.rec_events.enqueue_rec_event") as enqueue:
        emit_execution_rec_event(ex, wf)
        assert enqueue.called
        body = enqueue.call_args[0][0]
        assert body["type"] == "openagentui.execution.completed"


def test_emit_skipped_without_openrec_url(monkeypatch):
    monkeypatch.delenv("OPENREC_URL", raising=False)
    wf = Workflow.from_dict({"id": "wf1", "name": "Demo", "nodes": [], "edges": []})
    ex = WorkflowExecution(
        id="exec1", workflow_id="wf1", status="completed", started_at="now"
    )
    with patch("openagentui.rec_events.enqueue_rec_event") as enqueue:
        emit_execution_rec_event(ex, wf)
        enqueue.assert_not_called()
