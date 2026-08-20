---
name: open-app
description: "OpenAgents CLI, TUI, desktop, dashboard surfaces."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cli, tui, desktop, dashboard, gui]
    category: open-ecosystem
    related_skills: [open-ecosystem-hub, open-agentui, open-center]
---

# Open App (OpenAgents surfaces)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Client shells for **OpenAgents** — not OpenCenter (Phase 2 product GUI).

## When to Use

- Launch desktop, dashboard, TUI, gateway, or ACP
- Debug which surface fits a workflow
- OpenAgentUI dashboard REST (`:9119`)

For cross-module human GUI later → `open-center`.

## Prerequisites

- At least one OpenAgents profile configured (`openagents setup`)
- For dashboard/OpenAgentUI MCP: `openagents dashboard` running

## Procedure

| Surface | Command |
|---------|---------|
| CLI | `openagents` |
| TUI | `display.interface: tui` |
| Web admin | `openagents dashboard` |
| Desktop | `openagents desktop` |
| Gateway | `openagents gateway start` |
| IDE | `openagents acp` |
| Mobile hiring | `open-pro` (Flutter) |

## Procedure

1. Pick surface from user intent (terminal vs GUI vs messaging)
2. Ensure profile + MCP loaded for agent work
3. Dashboard required for OpenBrain → OpenAgentUI MCP proxy

## Decision rules

| User says | Launch |
|-----------|--------|
| Telegram bot | `gateway` |
| Local admin | `dashboard` |
| Daily driver GUI | `desktop` |
| OpenOS module Kanban | Defer — `open-center` Phase 2 |

## Pitfalls

- Building OpenTicket Kanban in OpenAgents web (defer OpenCenter)
- Expecting desktop without Electron deps built
- Confusing OpenAgents dashboard with OpenCenter

## Verification

- [ ] Chosen surface starts without auth errors
- [ ] MCP/tools available for intended agent task
- [ ] `OPENAGENTS_DASHBOARD_URL` set when using OpenBrain workflow MCP
