"""OpenPro TikTok prospection plugin tools."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from plugins.openpro_prospection.enrichment import build_company_brief, extract_email_from_lead
from plugins.openpro_prospection.openpro_client import (
    check_company_duplicate,
    create_job_post,
    provision_from_lead,
    send_prospect_email,
    send_tiktok_dm,
)
from plugins.openpro_prospection.openteam_client import report_prospection_status
from plugins.opencrm_sales.opencrm_client import check_account_duplicate as check_crm_account_duplicate
from plugins.opencrm_sales.opencrm_client import upsert_from_prospection_lead

CORRELATION_ENV = "PROSPECTION_CORRELATION_ID"


def _corr(args: Dict[str, Any]) -> str | None:
    return str(args.get("correlation_id") or os.environ.get(CORRELATION_ENV) or "") or None


def check_openpro_prospection_available() -> bool:
    return bool(os.environ.get("OPENPRO_AGENT_API_KEY", "").strip())


def handle_check_company_duplicate(args: Dict[str, Any]) -> str:
    """OpenPro duplicate check — also consults OpenCRM (CC-W1-006) so a lead already
    tracked as a CRM account (e.g. from a prior meeting) is not re-provisioned on OpenPro.
    """
    name = str(args.get("company_name") or "").strip()
    city = str(args.get("city") or "France").strip()
    if not name:
        return "Error: company_name is required"
    result = check_company_duplicate(name, city, _corr(args))
    crm_result = check_crm_account_duplicate(name, city)
    result["opencrm"] = crm_result
    if crm_result.get("duplicate"):
        result["duplicate"] = True
    return json.dumps(result, ensure_ascii=False)


def handle_provision_openpro_company(args: Dict[str, Any]) -> str:
    brief = str(args.get("brief") or "").strip()
    if not brief:
        return "Error: brief is required"
    payload = {
        "brief": brief,
        "company_name": args.get("company_name"),
        "city": args.get("city"),
        "video_url": args.get("video_url"),
        "tiktok_account": args.get("tiktok_account"),
    }
    result = provision_from_lead(payload, _corr(args))
    return json.dumps(result, ensure_ascii=False)


def handle_create_job_post_with_media(args: Dict[str, Any]) -> str:
    video_url = str(args.get("video_url") or "").strip()
    recruiter_id = str(args.get("recruiter_id") or args.get("openpro_recruiter_id") or "").strip()
    title = str(args.get("title") or "Offre recrutée depuis TikTok").strip()
    if not video_url or not recruiter_id:
        return "Error: video_url and recruiter_id are required"
    payload = {
        "recruiter_id": recruiter_id,
        "title": title,
        "content": str(args.get("content") or args.get("description") or title),
        "video_url": video_url,
        "source_url": video_url,
    }
    result = create_job_post(payload, _corr(args))
    return json.dumps(result, ensure_ascii=False)


def handle_send_prospect_email(args: Dict[str, Any]) -> str:
    email = str(args.get("email") or "").strip()
    if not email:
        return "Error: email is required"
    payload = {
        "email": email,
        "company_name": args.get("company_name"),
        "job_url": args.get("job_url"),
        "video_url": args.get("video_url"),
        "tiktok_account": args.get("tiktok_account"),
    }
    result = send_prospect_email(payload, _corr(args))
    return json.dumps(result, ensure_ascii=False)


def handle_send_tiktok_dm(args: Dict[str, Any]) -> str:
    payload = {
        "profile_url": args.get("profile_url"),
        "account": args.get("tiktok_account") or args.get("account"),
        "message": args.get("message"),
        "video_url": args.get("video_url"),
    }
    result = send_tiktok_dm(payload, _corr(args))
    return json.dumps(result, ensure_ascii=False)


def handle_report_prospection_status(args: Dict[str, Any]) -> str:
    video_url = str(args.get("video_url") or "").strip()
    status = str(args.get("status") or "").strip()
    if not video_url or not status:
        return "Error: video_url and status are required"
    payload = {
        "video_url": video_url,
        "status": status,
        "company_name": args.get("company_name"),
        "city": args.get("city"),
        "openpro_recruiter_id": args.get("openpro_recruiter_id"),
        "openpro_post_id": args.get("openpro_post_id"),
        "error": args.get("error"),
        "raw_outreach": args.get("raw_outreach"),
    }
    result = report_prospection_status(payload, _corr(args))
    return json.dumps(result, ensure_ascii=False)


def handle_upsert_crm_from_lead(args: Dict[str, Any]) -> str:
    """CC-W1-004 — upsert the enriched lead into OpenCRM (account + opportunity, stage lead)."""
    video_url = str(args.get("video_url") or "").strip()
    company_name = str(args.get("company_name") or "").strip()
    if not video_url or not company_name:
        return "Error: video_url and company_name are required"
    result = upsert_from_prospection_lead(
        video_url=video_url,
        company_name=company_name,
        city=args.get("city"),
        email=args.get("email"),
        tiktok_account=args.get("tiktok_account"),
        correlation_id=_corr(args),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_enrich_tiktok_lead(args: Dict[str, Any]) -> str:
    lead = args.get("lead")
    if not isinstance(lead, dict):
        return "Error: lead object is required"
    email = extract_email_from_lead(lead)
    brief = build_company_brief(lead, email)
    enriched = {
        "email": email,
        "brief": brief,
        "account": lead.get("account"),
        "video_url": lead.get("video_url"),
        "profile_url": lead.get("profile_url"),
        "description": lead.get("description"),
    }
    return json.dumps(enriched, ensure_ascii=False)


CHECK_DUPLICATE_SCHEMA: Dict[str, Any] = {
    "name": "check_company_duplicate",
    "description": "Check if an OpenPro company already exists (name + city).",
    "parameters": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "city": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["company_name"],
    },
}

PROVISION_SCHEMA: Dict[str, Any] = {
    "name": "provision_openpro_company",
    "description": "Create OpenPro recruiter account from TikTok lead brief.",
    "parameters": {
        "type": "object",
        "properties": {
            "brief": {"type": "string"},
            "company_name": {"type": "string"},
            "city": {"type": "string"},
            "video_url": {"type": "string"},
            "tiktok_account": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["brief"],
    },
}

CREATE_JOB_SCHEMA: Dict[str, Any] = {
    "name": "create_job_post_with_media",
    "description": "Create OpenPro job offer with TikTok video attached.",
    "parameters": {
        "type": "object",
        "properties": {
            "recruiter_id": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "description": {"type": "string"},
            "video_url": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["recruiter_id", "video_url"],
    },
}

EMAIL_SCHEMA: Dict[str, Any] = {
    "name": "send_prospect_email",
    "description": "Send scraped-email outreach with OpenPro job link.",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {"type": "string"},
            "company_name": {"type": "string"},
            "job_url": {"type": "string"},
            "video_url": {"type": "string"},
            "tiktok_account": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["email"],
    },
}

DM_SCHEMA: Dict[str, Any] = {
    "name": "send_tiktok_dm",
    "description": "Send or queue TikTok DM outreach (feature-flagged).",
    "parameters": {
        "type": "object",
        "properties": {
            "profile_url": {"type": "string"},
            "account": {"type": "string"},
            "tiktok_account": {"type": "string"},
            "message": {"type": "string"},
            "video_url": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["video_url"],
    },
}

STATUS_SCHEMA: Dict[str, Any] = {
    "name": "report_prospection_status",
    "description": "Update OpenTeam prospection lead status after processing.",
    "parameters": {
        "type": "object",
        "properties": {
            "video_url": {"type": "string"},
            "status": {
                "type": "string",
                "enum": [
                    "processing",
                    "provisioned",
                    "skipped_duplicate",
                    "skipped_no_email",
                    "failed",
                ],
            },
            "company_name": {"type": "string"},
            "city": {"type": "string"},
            "openpro_recruiter_id": {"type": "string"},
            "openpro_post_id": {"type": "string"},
            "error": {"type": "string"},
            "raw_outreach": {"type": "object"},
            "correlation_id": {"type": "string"},
        },
        "required": ["video_url", "status"],
    },
}

ENRICH_SCHEMA: Dict[str, Any] = {
    "name": "enrich_tiktok_lead",
    "description": "Extract email and build OpenPro provision brief from TikTok lead.",
    "parameters": {
        "type": "object",
        "properties": {"lead": {"type": "object"}},
        "required": ["lead"],
    },
}

UPSERT_CRM_SCHEMA: Dict[str, Any] = {
    "name": "upsert_crm_from_lead",
    "description": (
        "Upsert the enriched TikTok lead into OpenCRM as an account + opportunity "
        "(CC-W1-004) so it becomes queryable via search_accounts / search_observations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "video_url": {"type": "string"},
            "company_name": {"type": "string"},
            "city": {"type": "string"},
            "email": {"type": "string"},
            "tiktok_account": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["video_url", "company_name"],
    },
}
