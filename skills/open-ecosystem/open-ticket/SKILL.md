---
name: open-ticket
description: "OpenTicket MCP: create, read, transition tickets."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTicket, Jira, W4, MCP]
    category: open-ecosystem
    related_skills: [open-ticket-optimize, open-dev-workflow, open-code, openprotocol-coder, openprotocol-integrator]
---

# OpenTicket

Issue tracker — **source of truth** for agent work and W4 status.

Follow `open-ecosystem/OPERATING-STANCE.md`. If the user asks to open or update a ticket, call the MCP tool in this turn.

## When to Use

- PO creates backlog; Dev/QA transition tickets
- Any `OP-*` key or ticket UUID in context
- Webhook fired OpenOrchestrator dispatch

## Prerequisites

```bash
openagents mcp install openticket
export OPENTICKET_API_URL=http://localhost:3020
```

Profiles: `product_owner`, `developer`, `qa` via `openagents openos init-profiles`.

## MCP tools

| Tool | Profiles | Purpose |
|------|----------|---------|
| `create_ticket` | product_owner, security | Story/bug + AC |
| `get_ticket` | all | Read by UUID or `OP-42` |
| `update_ticket_status` | developer, qa | Workflow transitions |
| `update_ticket` | PO, dev | PATCH fields |
| `add_ticket_comment` | assigned | Handoff + summaries |
| `search_tickets` / `list_tickets` | all | Discovery |

Plugin only: `submit_ticket_result` — deliverables + optional `in_review`.

## Procedure — create any ticket

1. PO/planner: load `open-ticket-optimize` (Ticket Prompt Optimizer) — **mandatory**
2. Rewrite rough input into Task/Context/Complexity/Outcome/Keywords/Verification
3. `create_ticket` with optimized `title`, `description`, `acceptance_criteria[]`,
   `priority`, `execution_mode`, `assignee_agent_profile`
4. `update_ticket_status` → `todo` when ready to dispatch

## Procedure — W4 coding ticket

1. Follow **create any ticket** with `execution_mode: code`, assignee `developer`
2. Webhook → OpenOrchestrator → Dev run
3. Dev: `get_ticket` → `in_progress` → `invoke_opencode` (see `open-code`)
4. Dev: handoff comment + `in_review` (via webhook or `submit_ticket_result`)
5. QA: `openprotocol-integrator` → `done`

## Status matrix (OpenProtocol)

| From | To | Actor |
|------|-----|-------|
| backlog | todo | product_owner |
| todo | in_progress | developer |
| in_progress | in_review | developer / OpenCode webhook |
| in_review | done | qa only (after integrator merge) |

Invalid → `409 INVALID_TRANSITION`. Skip `qa` intermediate status when using integrator squash-merge path.

## Decision rules

| Ticket type | Path |
|-------------|------|
| `execution_mode: research` | OpenTeam/tools — not `invoke_opencode` |
| Code feature | W4 + OpenProtocol branches |
| Security | `open-sec` → ticket → W4 |

## Pitfalls

- Creating from raw text without `open-ticket-optimize`
- Dev transitioning to `done`
- Missing handoff comment (QA cannot find branch)
- AC empty on code tickets

## Verification

- [ ] `get_ticket` returns `correlation_id` + AC
- [ ] Status path matches matrix for W4
- [ ] Comment contains `OpenProtocol handoff` after implement
