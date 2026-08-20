---
name: open-center
description: "Human GUI shell composing OpenOS module APIs."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCenter, GUI, Phase2, compose]
    category: open-ecosystem
    related_skills: [open-ecosystem-hub, open-ticket, open-brain]
---

# OpenCenter (human shell — Phase 2)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

**Single product UI** for humans. Agents use MCP/REST; OpenCenter **displays** mesh state later.

## When to Use

- User asks for a dashboard, Kanban, or human approval UI across OpenOS
- Planning Phase 2 GUI — defer per-app dashboards

Do **not** build agent features only in OpenCenter — ship MCP + REST first (v0 rule).

## Structural overview

| Layer | v0 (now) | Phase 2 |
|-------|----------|---------|
| Agents | OpenAgents + MCP | unchanged |
| APIs | Per-app REST `:3020`… | unchanged |
| Human UI | none (CLI/MCP) | OpenCenter composes APIs |

OpenCenter **never** calls OpenCode directly — OpenOrchestrator → OpenAgents only.

## Prerequisites

- Target module v0 complete (MCP + REST + contracts verified)
- OpenBrain for doc context on UI contracts (when spec exists)

## Procedure (agent guidance now)

1. If user wants UI **today** → explain Phase 2; offer MCP/CLI path
2. If designing OpenCenter view → read module OpenAPI + ticket/approval APIs
3. Wire read-only panels first; writes go through same REST as agents
4. Human approvals → OpenOrchestrator staging endpoints

## Decision rules

| Request | Route |
|---------|-------|
| "Add Kanban to OpenTicket" | Defer — OpenCenter composes OpenTicket API |
| "Agent creates ticket" | `open-ticket` MCP now |
| "Approve CRM update" | OpenOrchestrator `/internal/staging/:id/apply` |

## Pitfalls

- Building duplicate GUI in each app repo
- OpenCenter calling OpenCode (forbidden — W4 rule)
- Skipping OpenContract because "it's just UI"

## Verification

- [ ] Feature exists on MCP **and** REST before GUI story
- [ ] No new mesh hop without `CC-*` registry row
- [ ] Agent path works without OpenCenter (OpenCenter is display-only)
