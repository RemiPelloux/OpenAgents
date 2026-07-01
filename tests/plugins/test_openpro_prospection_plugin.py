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

    with patch(
        "plugins.openpro_prospection.tools.check_company_duplicate",
        return_value={"duplicate": False, "matches": []},
    ) as mock:
        out = handle_check_company_duplicate({"company_name": "Cafe Paris", "city": "Paris"})
        mock.assert_called_once()
        assert json.loads(out)["duplicate"] is False


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
