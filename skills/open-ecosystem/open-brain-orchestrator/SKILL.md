---
name: open-brain-orchestrator
description: "OpenBrain mission control — start_mission, ask_brain ticket/ETA/DoD, live OpenTicket read."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenBrain, OpenTicket, OpenAgents, W4, orchestrator, mission]
    related_skills: [open-brain, open-dev-workflow, open-ticket, open-ecosystem-hub]
---

# OpenBrain Orchestrator

OpenBrain is the **command + visibility layer**. OpenAgents executes. OpenTicket is the trace spine.

## Flow

```
User → OpenBrain (ask_brain / start_mission)
  → OpenAgents PO profile (creates tickets, delegates team)
  → OpenTicket (correlation_id, AC/DoD, metadata.eta)
  → OpenRec trace → ask_brain answers
```

## MCP tools (OpenBrain Knowledge server)

| Tool | Use |
|------|-----|
| `start_mission` | Dispatch goal to OpenOrchestrator `/v1/missions` or OpenAgents `/v1/runs` (PO profile) |
| `get_ticket` | Live ticket JSON by key or UUID |
| `list_tickets` | Filter by status, assignee, `correlation_id` |
| `ask_brain` | RAG over docs + observations + **live ticket** + OpenRec trace |

Optional: `run_openagentui_workflow` when dashboard workflow exists.

## Env (OpenBrain API)

```bash
OPENTICKET_API_URL=http://localhost:3020
OPENTICKET_API_TOKEN=...
OPENORCHESTRATOR_API_URL=http://localhost:3050
OPENAGENTS_API_URL=http://localhost:8080
OPENREC_API_URL=http://localhost:3030
```

## Examples

**Start work**

```
start_mission(goal="Implement OAuth login", eta="2026-07-15T18:00:00Z")
```

**Ask status**

```
ask_brain(question="What is OP-42? ETA and definition of done?")
```

PO/Dev agents use `open-dev-workflow` + OpenTicket MCP for execution — Brain does not replace W4.

## Rules

- Every mission gets a `correlation_id` — propagate to tickets and OpenRec.
- Set `metadata.eta` on tickets for Brain ETA answers (PATCH or PO create).
- OpenNotes is optional — defer meeting→ticket until later.
