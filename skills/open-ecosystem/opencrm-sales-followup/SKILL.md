---
name: opencrm-sales-followup
description: "CRM read + staged follow-up via orchestrator approval."
version: 1.2.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCRM, sales, W1, staging]
    category: open-ecosystem
    related_skills: [opencrm-contact-enrichment, open-brain, open-team, openpro-tiktok-prospection]
---

# OpenCRM Sales Follow-up

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Read CRM truth + Axon context → **stage** next action (`CC-W1-003`) — never send directly.

For LinkedIn / décideur / phone enrichment, load **`opencrm-contact-enrichment`**.

## When to Use

- "What's the state of {Account}?"
- Draft next commercial step after meeting/prospection
- Agent must not email customer without approval

## Prerequisites

```bash
export OPENCRM_API_URL=http://localhost:3010
```

Plugin `opencrm_sales` + OpenCRM MCP + Brain MCP (`open-brain`).

## Tools

| Surface | Tool | Purpose |
|---------|------|---------|
| Plugin | `search_accounts` | Fuzzy company + city |
| Plugin | `get_account` | Full account + contacts |
| Plugin | `check_account_duplicate` | Before create (`CC-W1-006`) |
| Plugin | `propose_crm_update` | Stage change — pending approval |
| MCP | `get_customer_context` | Snapshot by name/email/contact |
| MCP | `enrich_contact` | Lead fields — see enrichment skill |
| MCP | `list_hot_leads` | Ranked account scores |

## Procedure

1. `search_accounts` or `get_account` / `get_customer_context`
2. If contact data thin → hand off to `opencrm-contact-enrichment`
3. `search_observations(app=opencrm, query=…)`
4. `search_knowledge(domain=openos, query=…)`
5. Draft follow-up from combined context
6. `propose_crm_update` — report staged id; **do not** send email

## Decision rules

| If | Then |
|----|------|
| `get_account.next_action` already set | Do not duplicate `propose_crm_update` |
| `goal_met: false` | Expected — apply via Orchestrator staging only |
| TikTok lead | `openpro-tiktok-prospection` may have upserted CRM first |
| Need LinkedIn/décideur | Use `opencrm-contact-enrichment` / `enrich_contact` |

## Pitfalls

- PII in Brain observations
- Calling customer APIs directly
- Duplicate staged updates for same next step

## Verification

- [ ] Staged update id returned from `propose_crm_update`
- [ ] `get_account` reflects read-only truth before write
- [ ] No outbound email tool invoked
