"""Tests for CC-W4-001 create_ticket producer signing."""

from __future__ import annotations

from unittest.mock import patch

from plugins.openos_engineering.ticket_client import W4_PO_CREATE, create_ticket
from plugins.openos_engineering.tools import handle_create_ticket


def test_create_ticket_wraps_cc_w4_001():
    with patch(
        "plugins.openos_engineering.ticket_client.wrap_signed_hop",
        return_value={"contract_id": W4_PO_CREATE, "payload": {}},
    ) as wrap_mock, patch(
        "plugins.openos_engineering.ticket_client._post_json",
        return_value={"id": "t1", "ticket_key": "OP-1", "correlation_id": "corr-1"},
    ):
        ticket = create_ticket("proj-1", "story", "Smoke test", acceptance_criteria=["pass"])

    assert wrap_mock.call_args.kwargs["contract_id"] == W4_PO_CREATE
    assert wrap_mock.call_args.kwargs["producer"] == "OpenAgents [product_owner]"
    assert ticket["ticket_key"] == "OP-1"


def test_handle_create_ticket_returns_key():
    with patch(
        "plugins.openos_engineering.tools.create_ticket",
        return_value={"ticket_key": "OP-42", "correlation_id": "corr-42"},
    ):
        result = handle_create_ticket(
            {"project_id": "p1", "type": "story", "title": "Test ticket"},
        )

    assert "OP-42" in result
    assert "corr-42" in result
