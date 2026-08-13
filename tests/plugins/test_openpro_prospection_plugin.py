"""Tests for openpro_prospection plugin."""

from __future__ import annotations

import json
import os
from unittest.mock import patch


def test_plugin_registers_tools():
    import plugins.openpro_prospection as plugin

    registered = []

    class Ctx:
        def register_tool(self, **kwargs):
            registered.append(kwargs["name"])

    plugin.register(Ctx())
    assert "check_company_duplicate" in registered
    assert "enrich_tiktok_lead" in registered
    assert "report_prospection_status" in registered


def test_crm_and_status_tools_do_not_require_openpro_key():
    import plugins.openpro_prospection as plugin

    registered = {}

    class Ctx:
        def register_tool(self, **kwargs):
            registered[kwargs["name"]] = kwargs

    plugin.register(Ctx())
    with patch.dict(os.environ, {}, clear=True):
        assert registered["upsert_crm_from_lead"]["check_fn"]() is True
        assert registered["check_company_duplicate"]["check_fn"]() is True
        assert registered["report_prospection_status"]["check_fn"]() is True
        assert registered["provision_openpro_company"]["check_fn"]() is False


def test_opencrm_client_adds_webhook_secret():
    from plugins.opencrm_sales.opencrm_client import _headers

    with patch.dict(os.environ, {"OPENTEAM_WEBHOOK_SECRET": "test-secret"}, clear=True):
        headers = _headers("corr-1", include_webhook_secret=True)
    assert headers["X-Webhook-Secret"] == "test-secret"
    assert headers["X-Correlation-Id"] == "corr-1"

    regular_headers = _headers("corr-1")
    assert "X-Webhook-Secret" not in regular_headers


def test_enrich_tiktok_lead_extracts_email():
    from plugins.openpro_prospection.tools import handle_enrich_tiktok_lead

    lead = {
        "account": "@cafe.paris",
        "video_url": "https://tiktok.com/@cafe.paris/video/1",
        "description": "On recrute un barista!",
        "raw": {"authorMeta": {"signature": "contact@cafe.paris"}},
    }
    result = json.loads(handle_enrich_tiktok_lead({"lead": lead}))
    assert result["email"] == "contact@cafe.paris"
    assert "TikTok recruiter" in result["brief"]


def test_check_duplicate_calls_openpro():
    from plugins.openpro_prospection.tools import handle_check_company_duplicate

    with (
        patch.dict(os.environ, {"OPENPRO_AGENT_API_KEY": "test-key"}, clear=True),
        patch(
            "plugins.openpro_prospection.tools.check_company_duplicate",
            return_value={"duplicate": False, "matches": []},
        ) as mock,
        patch(
            "plugins.openpro_prospection.tools.check_crm_account_duplicate",
            return_value={"duplicate": False, "matches": []},
        ),
    ):
        out = handle_check_company_duplicate({"company_name": "Cafe Paris", "city": "Paris"})
    mock.assert_called_once()
    assert json.loads(out)["duplicate"] is False


def test_check_duplicate_uses_crm_without_openpro_key():
    from plugins.openpro_prospection.tools import handle_check_company_duplicate

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("plugins.openpro_prospection.tools.check_company_duplicate") as openpro_mock,
        patch(
            "plugins.openpro_prospection.tools.check_crm_account_duplicate",
            return_value={"duplicate": True, "matches": [{"id": "account-1"}]},
        ),
    ):
        result = json.loads(
            handle_check_company_duplicate({"company_name": "Cafe Paris", "city": "Paris"})
        )

    openpro_mock.assert_not_called()
    assert result["duplicate"] is True
    assert result["available"] is False
    assert result["opencrm"]["matches"][0]["id"] == "account-1"


def test_status_schema_supports_crm_only_success():
    from plugins.openpro_prospection.tools import STATUS_SCHEMA

    statuses = STATUS_SCHEMA["parameters"]["properties"]["status"]["enum"]
    assert "crm_created" in statuses


def test_tiktok_profile_has_only_prospection_tools():
    from plugins.openpro_prospection.profiles import PROFILE_SPEC

    assert PROFILE_SPEC["toolsets"] == ["openpro_prospection"]
    assert "explicitly authorizes" in PROFILE_SPEC["soul"]


def test_upsert_crm_from_lead_requires_fields():
    from plugins.openpro_prospection.tools import handle_upsert_crm_from_lead

    assert "required" in handle_upsert_crm_from_lead({}).lower()


def test_upsert_crm_from_lead_calls_opencrm():
    from plugins.openpro_prospection.tools import handle_upsert_crm_from_lead

    with patch(
        "plugins.openpro_prospection.tools.upsert_from_prospection_lead",
        return_value={"payload": {"account_id": "a1", "opportunity_id": "o1"}},
    ) as mock:
        out = handle_upsert_crm_from_lead(
            {"video_url": "https://tiktok.com/v/1", "company_name": "Decathlon Nice", "city": "Nice"}
        )
        mock.assert_called_once()
        assert json.loads(out)["payload"]["account_id"] == "a1"


def test_availability_requires_api_key():
    from plugins.openpro_prospection.tools import check_openpro_prospection_available

    old = os.environ.pop("OPENPRO_AGENT_API_KEY", None)
    try:
        assert check_openpro_prospection_available() is False
        os.environ["OPENPRO_AGENT_API_KEY"] = "test-key"
        assert check_openpro_prospection_available() is True
    finally:
        if old:
            os.environ["OPENPRO_AGENT_API_KEY"] = old
        else:
            os.environ.pop("OPENPRO_AGENT_API_KEY", None)
