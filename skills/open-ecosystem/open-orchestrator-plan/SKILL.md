---
name: open-orchestrator-plan
description: "Submit goals, auto-plan, dispatch OpenAgents runs."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openorchestrator, planner, dispatch]
    category: open-ecosystem
    related_skills: [open-orchestrator-intent, open-dev-workflow, open-ecosystem-hub]
---

# Open Orchestrator Plan

Control plane: goals → plans → tasks → **OpenAgents** runs (never OpenCode direct).

## When to Use

- Multi-step objective needs decomposition + dispatch
- Manual override when ticket webhook already fired
- Polling plan/task status

## Prerequisites

- `ORCHESTRATOR_URL` (default `http://localhost:3050`)
- MCP `@openorchestrator/mcp-server` stdio
- Profiles: `planner`, `developer`, `qa` initialized

## MCP tools

| Tool | Purpose |
|------|---------|
| `orch_submit_goal` | Auto-plan + dispatch |
| `orch_get_plan_status` | Poll plan/tasks |
| `orch_list_tasks` | List tasks |
| `orch_override_route` | Manual assign (audited) |

REST: `POST /v1/goals`, `GET /v1/plans/:id/status`, `POST /v1/tasks/:id/assign`.

## Procedure

1. `orch_submit_goal(objective=…, ticket_id=optional)`
2. Note `plan_id` + `dispatched_task_ids`
3. `orch_get_plan_status` until complete or `blocked`
4. If blocked → `orch_override_route` or fix upstream skill gap (`open-mcp-scaffold`)

## Decision rules

| Header / mode | Behavior |
|---------------|----------|
| Default | Auto planner + dispatch |
| `X-OpenOS-Plan-Mode: manual` | Skip auto dispatch |
| Engineering ticket | Dispatch `developer` → W4 |
| `blocked` + capability_gap | `open-mcp-scaffold` |

## Pitfalls

- Calling OpenCode from orchestrator (forbidden)
- Missing `correlation_id` on dispatch
- Ignoring `blocked` without reading task error

## Verification

- [ ] `orch_get_plan_status` shows tasks `completed` or explicit `blocked` reason
- [ ] Dispatched profile matches goal class
- [ ] Ticket webhook path still has matching `correlation_id`
