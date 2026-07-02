---
name: open-orchestrator-plan
description: "Auto-plan goals and dispatch OpenAgents via Orchestrator."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openorchestrator, planner, auto-plan, W4]
    related_skills: [open-dev-workflow, open-ecosystem-hub]
---

# Open Orchestrator Plan

Agent-first control plane: submit goals, auto-decompose, dispatch profiles.

## When to Use

- Ticket webhook already fired but you need a **new multi-step plan**
- PO or agent submits a **natural-language objective**
- Manual routing override via MCP

## Prerequisites

- OpenOrchestrator API on `:3050` (or `ORCHESTRATOR_URL`)
- MCP server: `@openorchestrator/mcp-server` stdio
- Profiles: `openagents openos init-profiles` (includes `planner`)

## MCP tools

| Tool | Purpose |
|------|---------|
| `orch_submit_goal` | Auto-plan + dispatch (default) |
| `orch_get_plan_status` | Poll plan/tasks |
| `orch_list_tasks` | List in-memory tasks |
| `orch_override_route` | Manual assign (audit logged) |

## REST parity

| Method | Path |
|--------|------|
| POST | `/v1/goals` |
| GET | `/v1/plans/:id/status` |
| POST | `/v1/tasks/:id/assign` |

Set header `X-OpenOS-Plan-Mode: manual` to skip auto planner dispatch.

## Procedure

1. `orch_submit_goal` with `objective` (+ optional `ticket_id`)
2. Read `plan_id` and `dispatched_task_ids` from response
3. `orch_get_plan_status` until tasks complete or blocked
4. Use `orch_override_route` only when auto routing fails

## Rules

- OpenOrchestrator never calls OpenCode — OpenAgents only
- Default plan mode is **auto**; manual is override
- Propagate `correlation_id` on every hop
