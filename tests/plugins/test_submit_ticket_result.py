"""Tests for submit_ticket_result OpenTicket integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_submit_ticket_result_patches_and_comments():
    from plugins.openos_engineering.tools import handle_submit_ticket_result

    ticket = {
        "id": "t1",
        "ticket_key": "OP-9",
        "correlation_id": "corr-1",
        "status": "in_progress",
        "linked_agent_run_ids": [],
    }

    with patch(
        "plugins.openos_engineering.tools.get_ticket",
        return_value=ticket,
    ), patch(
        "plugins.openos_engineering.tools.patch_ticket",
        return_value={**ticket, "deliverables": [{"kind": "csv", "summary": "42 rows"}]},
    ) as patch_mock, patch(
        "plugins.openos_engineering.tools.add_ticket_comment",
        return_value={"id": "c1"},
    ) as comment_mock, patch(
        "plugins.openos_engineering.tools.update_ticket_status",
        return_value={**ticket, "status": "in_review"},
    ) as status_mock:
        result = handle_submit_ticket_result(
            {
                "ticket_id": "OP-9",
                "deliverables": [{"kind": "csv", "summary": "42 rows"}],
                "comment": "Scrape complete",
                "agent_run_id": "run-abc",
            }
        )

    assert "OP-9" in result
    patch_mock.assert_called_once()
    comment_mock.assert_called_once()
    status_mock.assert_called_once()


def test_submit_ticket_result_requires_deliverables():
    from plugins.openos_engineering.tools import handle_submit_ticket_result

    assert "deliverables" in handle_submit_ticket_result({"ticket_id": "OP-1"}).lower()
