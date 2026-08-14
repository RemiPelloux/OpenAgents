---
name: opencrm-contact-enrichment
description: "Enrich CRM contacts with LinkedIn and décideur data."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCRM, leads, enrichment, LinkedIn]
    category: open-ecosystem
    related_skills: [opencrm-sales-followup, open-brain, open-team]
---

# OpenCRM Contact Enrichment

Fill lead contact fields (email, mobile, LinkedIn, décideur) via MCP — keep PII in OpenCRM only.

## When to Use

- "Enrich this lead / find the décideur"
- Queue contacts missing LinkedIn or phone
- After prospection upsert, deepen primary contact

## Prerequisites

```bash
export OPENCRM_API_URL=http://localhost:3010
# MCP server: optional-mcps/opencrm (stdio)
```

Prefer **OpenCRM MCP** tools. Plugin `opencrm_sales` covers account search/staging only.

## MCP tools

| Tool | Purpose |
|------|---------|
| `list_contacts_needing_enrichment` | Queue: none/pending/partial/failed |
| `list_decision_makers` | Contacts with `is_decision_maker=true` |
| `search_contacts` | Filter `lead_status`, `enrichment_status`, décideur |
| `get_contact` / `create_contact` | Read or create lead shell |
| `enrich_contact` | **Primary write** — LinkedIn, décideur, phones, scores |
| `update_contact` | Generic edit when not an enrichment pass |

## Procedure

1. Resolve account: `search_accounts` / `get_account` / `get_account_hub`
2. After harvest or a new city location: `enrich_account` then `discover_account_people` (CC-W1-012)
3. Queue remaining gaps: `list_contacts_needing_enrichment` or `list_key_people`
4. Gather extra facts (OpenTeam harvest, meeting notes, public LinkedIn) — **no PII into Brain**
5. `enrich_contact(contact_id, …, mark_complete=true)` when confident
6. Optional: `propose_crm_update` on the **account** for next_action (approval)
7. Pair Brain: `search_observations(app=opencrm)` for narrative only

## Decision rules

| If | Then |
|----|------|
| Missing LinkedIn/email/mobile | Leave field null; set `enrichment_status=partial` |
| Confirmed budget owner | `is_decision_maker=true`, `buying_role=decision_maker` |
| Enrichment finished | `mark_complete=true` (sets complete + `lead_status=enriched`) |
| Outreach/email send | Hand off to `opencrm-sales-followup` staging — never send directly |

## Pitfalls

- Do not put email/phone/LinkedIn URLs into Brain observations
- Do not invent domains or titles — omit when unverified
- Prefer `enrich_contact` over scattering fields via `update_contact`

## Verification

- [ ] Contact shows LinkedIn / décideur / mobile as intended
- [ ] `enrichment_status` is `complete` or honest `partial`
- [ ] No PII written to OpenBrain
