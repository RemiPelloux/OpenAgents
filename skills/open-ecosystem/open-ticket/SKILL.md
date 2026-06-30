---
name: open-ticket
description: "OpenTicket MCP operations for PO, Dev, and QA — create, read, transition tickets."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTicket, Jira, W4, MCP]
    related_skills: [open-code, open-dev-workflow]
---

# OpenTicket

OpenTicket is the issue source of truth for the OpenOS W4 workflow.

## Setup

```bash
openagents mcp install openticket
```

Ensure API is running: `OPENTICKET_API_URL=http://localhost:3020`

## MCP tools

| Tool | Profiles | Purpose |
|------|----------|---------|
| `create_ticket` | product_owner, security | New story/bug with acceptance criteria |
| `get_ticket` | all | Read by UUID or key (`OP-42`) |
| `update_ticket_status` | developer, qa | Workflow transitions |
| `list_tickets` | all | Filter by status, profile, project |

## Status transition matrix

| From | To | Actor |
|------|-----|-------|
| backlog | todo | product_owner |
| todo | in_progress | developer |
| in_progress | in_review | developer, opencode callback |
| in_review | qa | developer |
| qa | done | qa only |

Invalid transitions return `409 INVALID_TRANSITION`.

## PO workflow

1. `create_ticket` with `acceptance_criteria` array
2. `update_ticket_status` → `todo` with `assignee_agent_profile: developer`
3. Orchestrator dispatches Dev agent automatically

## Dev workflow

1. `get_ticket` for context
2. `update_ticket_status` → `in_progress`
3. `invoke_opencode` (see `open-code` skill) — not an OpenTicket MCP tool

## QA workflow

1. `get_ticket` after OpenCode session-complete (`in_review`)
2. `invoke_opencode` mode=`test` or `review`
3. `update_ticket_status` → `done` (QA only)
