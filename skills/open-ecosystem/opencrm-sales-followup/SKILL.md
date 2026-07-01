---
name: opencrm-sales-followup
description: "OpenCRM sales follow-up agent — read account state, cross-reference Axon knowledge, and stage a follow-up (CC-W1-003) for OpenOrchestrator approval."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCRM, OpenBrain, Axon, W1, MCP, Sales]
    related_skills: [open-brain, open-ecosystem-hub, openpro-tiktok-prospection]
---

# OpenCRM Sales Follow-up

Drafts the **next commercial action** for an account by combining CRM state (source of
truth) with Axon knowledge (validated context) — never sends anything itself; every write
is staged and requires human approval via OpenOrchestrator.

## Setup

```bash
export OPENCRM_API_URL=http://localhost:3010
```

Ensure the OpenCRM API is running (`apps/api` in `OpenCRM/`, default port `3010`).

## Plugin tools (`opencrm_sales`)

| Tool | Purpose |
|------|---------|
| `search_accounts` | Fuzzy company name + city lookup (crm:read) |
| `check_account_duplicate` | Confirm an account already exists before creating one (CC-W1-006) |
| `get_account` | Read full account + contacts by id (crm:read) |
| `propose_crm_update` | Stage an account/opportunity change — pending until OpenOrchestrator approves (CC-W1-003) |

Combine with `search_knowledge` (`domain: openos`) and `search_observations` (`app: opencrm`)
from the **open-brain** skill for validated meeting history and prospection context.

## Workflow

1. `search_accounts` (or `get_account` if the id is already known) to resolve the company
2. `search_observations(app="opencrm", query="<company> <city>")` — meeting/prospection history
3. `search_knowledge(domain="openos", query="<company> commercial")` — validated facts
4. Draft the follow-up (next step, objections to address) from the combined context
5. `propose_crm_update` with `entity_type: "opportunity"`, `payload: { next_step, objections }`
6. Report the staged update id — do **not** send email or notify the customer directly

## Answering "What's the state of {Account}?"

```
search_accounts(company_name="Decathlon", city="Nice")
  → get_account(id)
  → search_observations(app="opencrm", query="Decathlon Nice")
  → search_knowledge(domain="openos", query="Decathlon commercial")
```

`get_account` is authoritative for current pipeline stage and owner; observations and
knowledge fill in meeting history and prospection provenance.

## Rules

- Never call `propose_crm_update` twice for the same next step — check `get_account.next_action`
  first to avoid duplicate staged updates.
- `propose_crm_update` always returns `goal_met: false` — it is staged, not applied. Applying
  happens only via OpenOrchestrator's `/internal/staging/:id/apply` callback.
- No PII (email/phone) in follow-up drafts placed into `search_knowledge`/observations.
