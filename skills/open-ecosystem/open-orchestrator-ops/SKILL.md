---
name: open-orchestrator-ops
description: "Industrial orchestrator: gates, approvals, DLQ."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openorchestrator, control-plane, military, capability, approval]
    category: open-ecosystem
    related_skills: [open-orchestrator-plan, open-orchestrator-intent, open-mcp-scaffold, open-mesh-wiring, open-rec]
---

# Open Orchestrator Ops (industrial grade)

**Control plane discipline** — plans do not run blind. Gates, approvals, capability remediation, audit.

Follow `open-ecosystem/OPERATING-STANCE.md`. Submit the goal or gate action in this turn.

## When to Use

- `capability_blocked: true` on goal response
- `approval_required: true` (risk ≥ 3)
- Plan status `waiting_capability` or `waiting_approval`
- Production deploy, external side effects, merge-to-main missions
- Skill-gap self-healing loop (C-sprint)

## Prerequisites

- `ORCHESTRATOR_URL` (default `http://localhost:3050`)
- MCP `openorchestrator` or REST parity
- Profiles: 16 canonical roles — `GET /v1/profiles` · auto `ensure` before dispatch
- OpenRec for `orchestrator.*` outcome events

## Structural overview

| Gate | Trigger | Unblock |
|------|---------|---------|
| Intent | NL goal | `open-orchestrator-intent` JSON |
| Policy | `risk_level` ≥ 3 | Human approval card |
| Capability | Missing MCP tool | `capability/approve` + deploy webhook |
| Profile gap | Unknown `agent_profile` | `POST /v1/profiles/ensure` or `skill_author` |
| Skill gap | `POST /v1/skill-gaps` | `open-mcp-scaffold` + `skill_author` |
| Outcome | Task complete/fail | `orch_get_plan_status` + OpenRec |

Plan states: `running` · `waiting_approval` · `waiting_capability` · `blocked` · `completed`.

## Procedure — submit goal (industrial)

1. `open-orchestrator-intent` if input is free text
2. `orch_submit_goal(objective, ticket_id?, correlation_id?)`
3. If `approval_required` → **STOP** — surface approval card; no bypass
4. If `capability_blocked` → read `capability_report.priority_order`
5. For each missing tool: `open-mcp-scaffold` ticket → deploy → webhook
6. `POST /v1/plans/:id/capability/approve` when mesh catalog updated
7. `POST /v1/webhooks/deploy/ready` after deploy verify
8. Poll `orch_get_plan_status` until `completed` or explicit `blocked`
9. Emit/query OpenRec for `orchestrator.outcome.*`

## Procedure — capability remediation loop

```
goal → capability_blocked
  → priority_order[0] highest missing tool
  → invoke_opencode / open-mesh-wiring implements tool
  → deploy/ready webhook
  → capability/approve
  → plan resumes dispatch
```

Script: `OpenOrchestrator/scripts/c-skill-gap-e2e.sh`

## Decision rules

| `risk_level` | Rule |
|--------------|------|
| 1–2 | Auto dispatch allowed |
| 3–4 | `approval_required` — human or staging gate |
| prod deploy | Never skip capability catalog check |

| `guided_next.action` | Agent does |
|----------------------|------------|
| `POST …/capability/approve` | Verify deploy then approve |
| `approval` | Wait for human |
| `open-mcp-scaffold` | File gap; do not fake tool |

## Pitfalls

- Bypassing `waiting_approval` with manual OpenCode
- Approving capability before deploy webhook proves tool live
- Dispatching OpenCode from orchestrator (forbidden — OpenAgents only)
- Losing `correlation_id` across plan tasks

## Verification

- [ ] `c-skill-gap-e2e.sh` green in CI when touching capability gate
- [ ] Plan reaches `completed` with OpenRec outcome event
- [ ] No task `completed` with missing required_tools in catalog snapshot
- [ ] High-risk goals have approval audit trail
