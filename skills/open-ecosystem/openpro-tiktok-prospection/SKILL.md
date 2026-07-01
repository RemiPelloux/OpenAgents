---
name: openpro-tiktok-prospection
description: "Prospect TikTok recruitment leads into OpenPro — enrich, dedupe, provision, job+video, email+DM outreach."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTeam, OpenPro, TikTok, Prospection, MCP]
    related_skills: [open-ecosystem-hub, opencrm-sales-followup]
---

# OpenPro TikTok Prospection

Process **today's TikTok harvest** from OpenTeam into OpenPro company accounts.

## Setup

```bash
export OPENTEAM_API_URL=http://localhost:8050
export PROSPECTION_API_KEY=your-webhook-secret
export OPENPRO_API_URL=https://api.openpro.ai
export OPENPRO_AGENT_API_KEY=your-agent-key
export PROSPECTION_CORRELATION_ID=optional-corr-id
```

## Plugin tools

| Tool | Purpose |
|------|---------|
| `enrich_tiktok_lead` | Extract email + build provision brief |
| `check_company_duplicate` | OpenPro + OpenCRM name+city duplicate check |
| `upsert_crm_from_lead` | Upsert account + opportunity into OpenCRM (CC-W1-004) |
| `provision_openpro_company` | Create recruiter account from brief |
| `create_job_post_with_media` | Publish job with TikTok video |
| `send_prospect_email` | Email outreach with job link |
| `send_tiktok_dm` | TikTok DM (feature-flagged / manual fallback) |
| `report_prospection_status` | PATCH OpenTeam lead status |

## Workflow (per lead)

1. `enrich_tiktok_lead` with lead object from `task_context.leads[]`
2. Infer `company_name` + `city` from brief (LLM) — ask if unclear
3. `check_company_duplicate` — also queries OpenCRM (CC-W1-006, degrades gracefully if
   OpenCRM is unreachable); if duplicate on either side → `report_prospection_status` `skipped_duplicate`
4. If no outreach email → `skipped_no_email`
5. `upsert_crm_from_lead` — OpenCRM now holds the account (queryable via `search_accounts`
   and Brain `search_observations app=opencrm`) even if OpenPro provisioning fails below
6. `provision_openpro_company` with brief
7. `create_job_post_with_media` using returned `recruiter_id`
8. `send_prospect_email` to scraped email
9. `send_tiktok_dm` with French intro + job URL
10. `report_prospection_status` → `provisioned` with IDs

## Status values

- `processing` — started
- `provisioned` — account + job + outreach done
- `skipped_duplicate` — company exists on OpenPro
- `skipped_no_email` — no real outreach email found
- `failed` — error (include `error` field)

## Trigger

OpenTeam Telegram button **Prospecter la récolte du jour** dispatches `task_context` via `POST /v1/runs`.

Only process leads in `task_context.leads` — never re-process URLs already marked terminal in OpenTeam.
