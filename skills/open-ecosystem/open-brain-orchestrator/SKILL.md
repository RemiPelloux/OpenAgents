---
name: open-brain-orchestrator
description: "Brain missions, ticket ETA, ask_brain status queries."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openbrain, mission, orchestrator, W4]
    category: open-ecosystem
    related_skills: [open-brain, open-dev-workflow, open-ticket, open-rec]
---

# OpenBrain Orchestrator

Command + visibility layer: missions, live tickets, RAG answers. **Execution** stays in OpenAgents W4.

## When to Use

- User starts a mission from Brain UI or `start_mission`
- Status questions: "What is OP-42 ETA and DoD?"
- Correlating mission → tickets → audit

## Prerequisites

```bash
OPENTICKET_API_URL=http://localhost:3020
OPENORCHESTRATOR_API_URL=http://localhost:3050
OPENAGENTS_API_URL=http://localhost:8080
OPENREC_API_URL=http://localhost:3030
```

Brain Knowledge MCP with agent key.

## MCP tools

| Tool | Purpose |
|------|---------|
| `start_mission` | Dispatch to Orchestrator or OpenAgents PO |
| `get_ticket` / `list_tickets` | Live ticket JSON |
| `set_ticket_eta` / `get_ticket_eta` | ETA + rollup |
| `ask_brain` | RAG + live ticket + Rec trace |

## Procedure

1. `start_mission(goal, eta=optional)` → captures `correlation_id`
2. PO path creates tickets (see `open-ticket`)
3. `ask_brain("OP-42 status?")` for operators
4. Execution: `open-dev-workflow` — Brain does not replace Dev/OpenCode

## Decision rules

| User asks | Route |
|-----------|-------|
| Start work | `start_mission` |
| Implement code | W4 via OpenAgents (not Brain direct) |

## Pitfalls

- Brain agent writing code without ticket + W4
- Missing ETA on mission (auto-default by priority if omitted)
- Deferred connector paths confused with W4

## Verification

- [ ] Mission returns `correlation_id`
- [ ] `get_ticket` matches live OpenTicket API
- [ ] `ask_brain` cites doc paths for spec questions
