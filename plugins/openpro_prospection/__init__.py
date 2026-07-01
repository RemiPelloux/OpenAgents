"""OpenPro TikTok prospection plugin."""

from __future__ import annotations

from plugins.openpro_prospection.tools import (
    CHECK_DUPLICATE_SCHEMA,
    CREATE_JOB_SCHEMA,
    DM_SCHEMA,
    EMAIL_SCHEMA,
    ENRICH_SCHEMA,
    PROVISION_SCHEMA,
    STATUS_SCHEMA,
    check_openpro_prospection_available,
    handle_check_company_duplicate,
    handle_create_job_post_with_media,
    handle_enrich_tiktok_lead,
    handle_provision_openpro_company,
    handle_report_prospection_status,
    handle_send_prospect_email,
    handle_send_tiktok_dm,
)


def register(ctx) -> None:
    tools = [
        (CHECK_DUPLICATE_SCHEMA, handle_check_company_duplicate, "🔍"),
        (ENRICH_SCHEMA, handle_enrich_tiktok_lead, "🧩"),
        (PROVISION_SCHEMA, handle_provision_openpro_company, "🏢"),
        (CREATE_JOB_SCHEMA, handle_create_job_post_with_media, "📋"),
        (EMAIL_SCHEMA, handle_send_prospect_email, "✉️"),
        (DM_SCHEMA, handle_send_tiktok_dm, "💬"),
        (STATUS_SCHEMA, handle_report_prospection_status, "📌"),
    ]
    for schema, handler, emoji in tools:
        ctx.register_tool(
            name=schema["name"],
            toolset="openpro_prospection",
            schema=schema,
            handler=handler,
            check_fn=check_openpro_prospection_available,
            emoji=emoji,
        )
