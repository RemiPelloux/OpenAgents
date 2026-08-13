"""OpenPro TikTok prospection plugin."""

from __future__ import annotations

from plugins.openpro_prospection.tools import (
    CHECK_DUPLICATE_SCHEMA,
    CREATE_JOB_SCHEMA,
    DM_SCHEMA,
    EMAIL_SCHEMA,
    ENRICH_SCHEMA,
    FILTER_LEADS_SCHEMA,
    PROVISION_SCHEMA,
    STATUS_SCHEMA,
    UPSERT_CRM_SCHEMA,
    check_openpro_prospection_available,
    handle_check_company_duplicate,
    handle_create_job_post_with_media,
    handle_enrich_tiktok_lead,
    handle_filter_tiktok_leads,
    handle_provision_openpro_company,
    handle_report_prospection_status,
    handle_send_prospect_email,
    handle_send_tiktok_dm,
    handle_upsert_crm_from_lead,
)


def _always_available() -> bool:
    return True


def register(ctx) -> None:
    tools = [
        (CHECK_DUPLICATE_SCHEMA, handle_check_company_duplicate, "🔍", _always_available),
        (FILTER_LEADS_SCHEMA, handle_filter_tiktok_leads, "🧹", _always_available),
        (ENRICH_SCHEMA, handle_enrich_tiktok_lead, "🧩", _always_available),
        (UPSERT_CRM_SCHEMA, handle_upsert_crm_from_lead, "🗂️", _always_available),
        (PROVISION_SCHEMA, handle_provision_openpro_company, "🏢"),
        (CREATE_JOB_SCHEMA, handle_create_job_post_with_media, "📋"),
        (EMAIL_SCHEMA, handle_send_prospect_email, "✉️"),
        (DM_SCHEMA, handle_send_tiktok_dm, "💬"),
        (STATUS_SCHEMA, handle_report_prospection_status, "📌", _always_available),
    ]
    for item in tools:
        schema, handler, emoji, *check = item
        ctx.register_tool(
            name=schema["name"],
            toolset="openpro_prospection",
            schema=schema,
            handler=handler,
            check_fn=check[0] if check else check_openpro_prospection_available,
            emoji=emoji,
        )
