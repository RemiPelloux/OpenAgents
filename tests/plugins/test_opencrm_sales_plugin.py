"""Tests for opencrm_sales plugin."""

from __future__ import annotations

import json
from unittest.mock import patch


def test_plugin_registers_tools():
    import plugins.opencrm_sales as plugin

    registered = []

    class Ctx:
        def register_tool(self, **kwargs):
            registered.append(kwargs["name"])

    plugin.register(Ctx())
    assert "search_accounts" in registered
    assert "check_account_duplicate" in registered
    assert "get_account" in registered
    assert "propose_crm_update" in registered


def test_handle_search_accounts_requires_company_name():
    from plugins.opencrm_sales.tools import handle_search_accounts

    assert "required" in handle_search_accounts({}).lower()


def test_handle_search_accounts_calls_opencrm():
    from plugins.opencrm_sales.tools import handle_search_accounts

    with patch(
        "plugins.opencrm_sales.tools.search_accounts",
        return_value={"accounts": [{"id": "a1", "company_name": "Decathlon Nice"}]},
    ) as mock:
        out = handle_search_accounts({"company_name": "Decathlon", "city": "Nice"})
        mock.assert_called_once_with("Decathlon", "Nice")
        assert json.loads(out)["accounts"][0]["id"] == "a1"


def test_handle_check_account_duplicate_calls_opencrm():
    from plugins.opencrm_sales.tools import handle_check_account_duplicate

    with patch(
        "plugins.opencrm_sales.tools.check_account_duplicate",
        return_value={"duplicate": True, "account": {"id": "a1"}},
    ) as mock:
        out = handle_check_account_duplicate({"company_name": "Decathlon Nice"})
        mock.assert_called_once()
        assert json.loads(out)["duplicate"] is True


def test_handle_get_account_requires_id():
    from plugins.opencrm_sales.tools import handle_get_account

    assert "required" in handle_get_account({}).lower()


def test_handle_propose_crm_update_requires_fields():
    from plugins.opencrm_sales.tools import handle_propose_crm_update

    assert "required" in handle_propose_crm_update({"entity_type": "account"}).lower()


def test_handle_propose_crm_update_calls_opencrm():
    from plugins.opencrm_sales.tools import handle_propose_crm_update

    with patch(
        "plugins.opencrm_sales.tools.propose_crm_update",
        return_value={"status": "success", "payload": {"staged_update_id": "s1"}},
    ) as mock:
        out = handle_propose_crm_update(
            {
                "org_id": "org-1",
                "entity_type": "opportunity",
                "entity_id": "opp-1",
                "payload": {"next_step": "send follow-up email"},
            }
        )
        mock.assert_called_once()
        assert json.loads(out)["payload"]["staged_update_id"] == "s1"


def test_check_account_duplicate_degrades_gracefully_when_unreachable():
    from plugins.opencrm_sales.opencrm_client import check_account_duplicate

    with patch(
        "plugins.opencrm_sales.opencrm_client.search_accounts",
        side_effect=OSError("connection refused"),
    ):
        result = check_account_duplicate("Decathlon Nice", "Nice")
    assert result == {"duplicate": False, "opencrm_unavailable": True}
