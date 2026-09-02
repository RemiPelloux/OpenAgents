"""OpenCRM sales-followup plugin tools (Sprint 7 — W1)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from plugins.opencrm_sales.opencrm_client import (
    check_account_duplicate,
    enrich_contact,
    get_account,
    list_decision_makers,
    propose_crm_update,
    search_accounts,
)


def check_opencrm_available() -> bool:
    return (
        bool(os.environ.get("OPENCRM_API_URL", "").strip()) or True
    )  # defaults to localhost:3010 in dev


def handle_search_accounts(args: Dict[str, Any]) -> str:
    company_name = str(args.get("company_name") or "").strip()
    if not company_name:
        return "Error: company_name is required"
    result = search_accounts(company_name, args.get("city"))
    return json.dumps(result, ensure_ascii=False)


def handle_check_account_duplicate(args: Dict[str, Any]) -> str:
    company_name = str(args.get("company_name") or "").strip()
    if not company_name:
        return "Error: company_name is required"
    result = check_account_duplicate(company_name, args.get("city"))
    return json.dumps(result, ensure_ascii=False)


def handle_get_account(args: Dict[str, Any]) -> str:
    account_id = str(args.get("account_id") or "").strip()
    if not account_id:
        return "Error: account_id is required"
    result = get_account(account_id)
    return json.dumps(result, ensure_ascii=False)


def handle_propose_crm_update(args: Dict[str, Any]) -> str:
    entity_type = str(args.get("entity_type") or "").strip()
    entity_id = str(args.get("entity_id") or "").strip()
    org_id = str(args.get("org_id") or "").strip()
    payload = args.get("payload")
    if entity_type not in ("account", "opportunity") or not entity_id or not org_id:
        return "Error: entity_type (account|opportunity), entity_id, and org_id are required"
    if not isinstance(payload, dict):
        return "Error: payload object is required"
    result = propose_crm_update(
        entity_type,
        entity_id,
        payload,
        org_id=org_id,
        agent_profile=str(args.get("agent_profile") or "sales-followup"),
        correlation_id=args.get("correlation_id"),
    )
    return json.dumps(result, ensure_ascii=False)


SEARCH_ACCOUNTS_SCHEMA: Dict[str, Any] = {
    "name": "search_accounts",
    "description": "Fuzzy-search OpenCRM accounts by company name and optional city (crm:read).",
    "parameters": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "city": {"type": "string"},
        },
        "required": ["company_name"],
    },
}

CHECK_DUPLICATE_SCHEMA: Dict[str, Any] = {
    "name": "check_account_duplicate",
    "description": "Check if an OpenCRM account already exists before creating one (CC-W1-006, crm:read).",
    "parameters": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "city": {"type": "string"},
        },
        "required": ["company_name"],
    },
}

GET_ACCOUNT_SCHEMA: Dict[str, Any] = {
    "name": "get_account",
    "description": "Read an OpenCRM account by id, including contacts (crm:read).",
    "parameters": {
        "type": "object",
        "properties": {"account_id": {"type": "string"}},
        "required": ["account_id"],
    },
}

PROPOSE_UPDATE_SCHEMA: Dict[str, Any] = {
    "name": "propose_crm_update",
    "description": (
        "Propose a staged update to an OpenCRM account or opportunity — requires "
        "OpenOrchestrator approval before it applies (CC-W1-003, crm:write)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {"type": "string"},
            "entity_type": {"type": "string", "enum": ["account", "opportunity"]},
            "entity_id": {"type": "string"},
            "payload": {"type": "object"},
            "agent_profile": {"type": "string"},
            "correlation_id": {"type": "string"},
        },
        "required": ["org_id", "entity_type", "entity_id", "payload"],
    },
}


def handle_enrich_contact(args: Dict[str, Any]) -> str:
    contact_id = str(args.get("contact_id") or "").strip()
    if not contact_id:
        return "Error: contact_id is required"
    fields = {
        key: value
        for key, value in args.items()
        if key not in {"contact_id", "org_id", "mark_complete"} and value is not None
    }
    result = enrich_contact(
        contact_id,
        fields,
        org_id=str(args["org_id"]).strip() if args.get("org_id") else None,
        mark_complete=bool(args.get("mark_complete")),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_list_decision_makers(args: Dict[str, Any]) -> str:
    result = list_decision_makers(
        org_id=str(args["org_id"]).strip() if args.get("org_id") else None,
        account_id=str(args["account_id"]).strip() if args.get("account_id") else None,
        limit=int(args.get("limit") or 50),
    )
    return json.dumps(result, ensure_ascii=False)


ENRICH_CONTACT_SCHEMA: Dict[str, Any] = {
    "name": "enrich_contact",
    "description": (
        "Enrich an OpenCRM contact/lead (LinkedIn, décideur, phones, scores). "
        "Prefer for enrichment workflows (crm:write)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contact_id": {"type": "string"},
            "org_id": {"type": "string"},
            "mark_complete": {"type": "boolean"},
            "email": {"type": "string"},
            "mobile": {"type": "string"},
            "linkedin_url": {"type": "string"},
            "is_decision_maker": {"type": "boolean"},
            "buying_role": {"type": "string"},
            "role": {"type": "string"},
            "lead_status": {"type": "string"},
            "enrichment_status": {"type": "string"},
            "confidence_score": {"type": "integer"},
            "notes": {"type": "string"},
        },
        "required": ["contact_id"],
    },
}

LIST_DECISION_MAKERS_SCHEMA: Dict[str, Any] = {
    "name": "list_decision_makers",
    "description": "List OpenCRM contacts flagged as decision makers (décideurs) (crm:read).",
    "parameters": {
        "type": "object",
        "properties": {
            "org_id": {"type": "string"},
            "account_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
}
