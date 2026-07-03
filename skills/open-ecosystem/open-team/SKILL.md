---
name: open-team
description: "OpenTeam harvests, market intel, and agent dispatch."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTeam, harvest, market, VPS, mesh]
    category: open-ecosystem
    related_skills: [openpro-tiktok-prospection, opencrm-sales-followup, open-ecosystem-hub]
---

# OpenTeam (market intelligence)

OpenTeam runs on **OVH VPS** — harvests content/leads and dispatches OpenAgents runs.

## When to Use

- TikTok/social harvest → agent processing (see also `openpro-tiktok-prospection`)
- Content engine jobs, lead status callbacks
- OpenTeam → OpenAgents gateway dispatch

Do **not** confuse with OpenPro mobile app — use `open-pro` for Flutter UX.

## Structural overview

| Hop | Mechanism | Secret |
|-----|-----------|--------|
| OpenTeam → OpenAgents | `POST /v1/runs` | `OPENAGENTS_API_KEY` |
| OpenAgents → OpenTeam | status webhook | `PROSPECTION_API_KEY` |
| OpenAgents → OpenPro | agent API | `OPENPRO_AGENT_API_KEY` |

Doc: `OpenOS/infra/docs/secrets-mesh.md`

## Prerequisites

```bash
OPENAGENTS_GATEWAY_URL=http://<host>:8642
OPENAGENTS_API_KEY=<from SSM>
OPENTEAM_API_URL=https://teamopeng.online
PROSPECTION_API_KEY=<shared with OpenAgents>
```

Without `OPENAGENTS_API_KEY`, prospection runs **dry-run** (leads recorded, no dispatch).

## Procedure

1. Harvest completes in OpenTeam (cron or manual)
2. Operator triggers agent dispatch with `task_context.leads[]`
3. OpenAgents plugin processes per lead workflow
4. Agent calls `report_prospection_status` (or equivalent) back to OpenTeam

## Decision rules

| Task | Skill |
|------|-------|
| TikTok → OpenPro provisioning | `openpro-tiktok-prospection` |
| CRM account follow-up | `opencrm-sales-followup` |
| Generic harvest analysis | `open-team` + `delegate_task` |

## Pitfalls

- Putting `OPENAGENTS_API_KEY` inside OpenAgents `.env` (inbound only — belongs on OpenTeam)
- Re-processing terminal leads
- Missing `correlation_id` on prospection batch

## Verification

- [ ] `POST /v1/runs` returns `run_id` from OpenTeam host
- [ ] Status callback authenticated with `PROSPECTION_API_KEY`
- [ ] Lead reaches terminal status exactly once
