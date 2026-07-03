---
name: opencrm-sales-followup
description: "CRM read + staged follow-up via orchestrator approval."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCRM, sales, W1, staging]
    category: open-ecosystem
    related_skills: [open-brain, open-team, openpro-tiktok-prospection]
---

# OpenCRM Sales Follow-up

Read CRM truth + Axon context → **stage** next action (`CC-W1-003`) — never send directly.

## When to Use

- "What's the state of {Account}?"
- Draft next commercial step after meeting/prospection
- Agent must not email customer without approval

## Prerequisites

```bash
export OPENCRM_API_URL=http://localhost:3010
```

Plugin `opencrm_sales` + Brain MCP (`open-brain`).

## Plugin tools

| Tool | Purpose |
|------|---------|
| `search_accounts` | Fuzzy company + city |
| `get_account` | Full account + contacts |
| `check_account_duplicate` | Before create (`CC-W1-006`) |
| `propose_crm_update` | Stage change — pending approval |

## Procedure

1. `search_accounts` or `get_account`
2. `search_observations(app=opencrm, query=…)`
3. `search_knowledge(domain=openos, query=…)`
4. Draft follow-up from combined context
5. `propose_crm_update` — report staged id; **do not** send email

## Decision rules

| If | Then |
|----|------|
| `get_account.next_action` already set | Do not duplicate `propose_crm_update` |
| `goal_met: false` | Expected — apply via Orchestrator staging only |
| TikTok lead | `openpro-tiktok-prospection` may have upserted CRM first |

## Pitfalls

- PII in Brain observations
- Calling customer APIs directly
- Duplicate staged updates for same next step

## Verification

- [ ] Staged update id returned from `propose_crm_update`
- [ ] `get_account` reflects read-only truth before write
- [ ] No outbound email tool invoked
