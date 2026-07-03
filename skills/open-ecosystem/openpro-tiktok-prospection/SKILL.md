---
name: openpro-tiktok-prospection
description: "TikTok harvest leads into OpenPro accounts."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTeam, OpenPro, TikTok, prospection]
    category: open-ecosystem
    related_skills: [open-team, opencrm-sales-followup, open-contract]
---

# OpenPro TikTok Prospection

Process **today's TikTok harvest** from OpenTeam into OpenPro + OpenCRM.

## When to Use

- `task_context.leads[]` from OpenTeam `POST /v1/runs`
- Telegram **Prospecter la récolte du jour**
- Lead status not yet terminal

## Prerequisites

```bash
OPENTEAM_API_URL=http://localhost:8050
PROSPECTION_API_KEY=...
OPENPRO_API_URL=https://api.openpro.ai
OPENPRO_AGENT_API_KEY=...
```

Plugin `openpro_prospection` enabled.

## Procedure (per lead)

1. `enrich_tiktok_lead`
2. Infer `company_name` + `city` — ask if unclear
3. `check_company_duplicate` → if dup: `skipped_duplicate`
4. If no email → `skipped_no_email`
5. `upsert_crm_from_lead` (`CC-W1-004`)
6. `provision_openpro_company`
7. `create_job_post_with_media`
8. `send_prospect_email` + optional `send_tiktok_dm`
9. `report_prospection_status` → `provisioned`

## Decision rules

| Status | Meaning |
|--------|---------|
| `provisioned` | Success |
| `skipped_duplicate` | OpenPro or CRM duplicate |
| `skipped_no_email` | No outreach email |
| `failed` | Include `error` field |

Only process leads in `task_context.leads` — never re-run terminal URLs.

## Pitfalls

- Re-processing completed leads
- Missing `PROSPECTION_CORRELATION_ID` on batch audit
- OpenCRM down — upsert degrades; still report status honestly

## Verification

- [ ] Terminal status reported once per lead URL
- [ ] CRM account exists via `search_accounts` when upsert ran
- [ ] `CC-W1-004` contract honored in registry
