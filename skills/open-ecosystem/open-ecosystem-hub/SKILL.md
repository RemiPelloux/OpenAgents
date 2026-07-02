---
name: open-ecosystem-hub
description: "Use when working across the Open product suite — routes to OpenAgents, OpenCode, OpenTicket, Open Pro, Open Brain, Open Memory, Open Whistle, or Open App."
version: 1.1.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [open, ecosystem, openagents, opencode, openticket, openpro, openbrain, openmemory, openwhistle, routing]
    related_skills: [openagents, open-code, open-ticket, open-dev-workflow, open-pro, openpro-tiktok-prospection, open-brain, open-brain-orchestrator, open-memory, open-whistle, open-app, open-agentui]
---

# Open Ecosystem Hub

The **Open** suite is a family of self-hosted and product-grade tools built around AI agents, professional workflows, persistent memory, and compliance. Use this hub to pick the right skill before diving in.

## Product map

| Product | Skill | One-line purpose |
|---------|-------|------------------|
| **OpenAgents** | `openagents` | Multi-surface AI agent (CLI, gateway, desktop, web) |
| **OpenCode** | `open-code` | OpenOS engineering co-pilot — headless coding via W4 |
| **OpenTicket** | `open-ticket` | Issue tracker (Jira) — PO/Dev/QA ticket workflow |
| **Open Pro** | `open-pro` | Flutter mobile app — candidate & recruiter hiring platform |
| **OpenPro TikTok Prospection** | `openpro-tiktok-prospection` | OpenTeam harvest → OpenPro accounts + outreach |
| **Open Brain** | `open-brain` | Shared persistent memory infrastructure (SQL + vectors + MCP) |
| **OpenAgentUI** | `open-agentui` | Visual/headless OpenAgents workflows (YAML, MCP, self-authoring) |
| **Open Memory** | `open-memory` | Agent memory inside OpenAgents + bridges to Open Brain |
| **Open Whistle** | `open-whistle` | Self-hosted whistleblower reporting (HinSchG / EU compliant) |
| **Open App** | `open-app` | Client surfaces — desktop, web dashboard, TUI, mobile shells |

## When to use this hub

- User mentions "Open" products without naming one specifically
- Task spans multiple products (e.g. "agent remembers hiring prefs in Open Pro")
- You need the correct repo, CLI, or integration path

## Routing rules

1. **Agent behavior, tools, gateway, cron, skills** → load `openagents` (bundled under `skills/autonomous-ai-agents/hermes-agent/`)
2. **Ticket → Dev → OpenCode → QA (W4)** → load `open-dev-workflow`, then `open-ticket` + `open-code`
3. **Coding via OpenOS OpenCode fork** → load `open-code` (not npm `opencode` skill)
4. **Tickets, backlog, acceptance criteria** → load `open-ticket`
5. **Flutter / OpenPro-Mobile / candidate-recruiter flows** → load `open-pro`
6. **Cross-tool memory database, MCP memory server, pgvector** → load `open-brain`
7. **OpenAgents `memory_*` tools, Honcho, session recall, `~/.openagents/memory`** → load `open-memory`
8. **Whistleblower channel, HinSchG, case reports, SDKs** → load `open-whistle`
9. **Desktop app, web dashboard, TUI, "open the app"** → load `open-app`
10. **Visual or headless multi-step agent workflows (YAML, run/approve)** → load `open-agentui`

## Typical cross-product flows

### OpenBrain builds an OpenAgentUI workflow

```
User asks OpenBrain agent → ensure_openagentui_workflow (MCP)
  → OpenAgents dashboard REST → ~/.openagents/openagentui/workflows/
  → run_openagentui_workflow (smoke test)
```

Requires `openagents dashboard` + `OPENAGENTS_DASHBOARD_URL` in OpenBrain.

### Agent + shared memory

```
Open Brain (source of truth) ← MCP → OpenAgents (open-memory skill)
```

Configure Open Brain MCP in OpenAgents (`openagents mcp` or `~/.openagents/config.yaml`), then use agent memory tools for session-local facts and Open Brain for durable cross-session knowledge.

### Hiring workflow

```
Open Pro (mobile UX) ← API → backend services
OpenAgents (automation) ← optional: cron, gateway, delegate subagents
```

Use `open-pro` for UI/code changes; use `openagents` for agent-side automation (screening drafts, calendar prep, Slack/Telegram notifications).

### Compliance reporting

```
Open Whistle (secure intake) — standalone FastAPI app
OpenAgents — optional triage assistant (never store raw reports in agent memory without redaction)
```

Keep whistleblower identity out of general agent memory; use `open-whistle` APIs via official SDKs only.

## Repositories (maintainer reference)

| Product | Primary repo |
|---------|--------------|
| OpenAgents | https://github.com/RemiPelloux/OpenAgents |
| Open Whistle SDKs | https://github.com/RemiPelloux/openwhistle-sdks |
| Open Whistle (upstream app) | https://github.com/openwhistle/OpenWhistle |
| Open Brain (reference) | https://github.com/NateBJones-Projects/OB1 |
| Open Pro | `OpenPro-Mobile/` (Flutter monorepo path in your workspace) |

## Verification checklist

- [ ] Identified which Open product owns the user request
- [ ] Loaded the specific skill (`open-pro`, `open-brain`, etc.) — not just this hub
- [ ] Cross-product tasks document data boundaries (memory, PII, compliance)
- [ ] OpenAgents tasks use `openagents` CLI, not legacy `hermes` unless migrating
