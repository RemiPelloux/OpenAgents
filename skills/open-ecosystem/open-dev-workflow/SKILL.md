---
name: open-dev-workflow
description: "W4 playbook: PO → OpenTicket → Dev → OpenCode → QA with correlation IDs and audit."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [W4, Workflow, OpenTicket, OpenCode, OpenAgents]
    related_skills: [open-code, open-ticket, open-ecosystem-hub, openprotocol-coder, openprotocol-integrator]
---

# Open Dev Workflow (W4)

End-to-end: **Axon context → PO ticket → Dev OpenCode (OpenProtocol coder) → QA integrator → OpenRec audit**.

## Profiles

Scaffold with:

```bash
openagents openos init-profiles
```

| Profile | Role | OpenTicket | OpenCode / Git |
|---------|------|------------|----------------|
| `product_owner` | Write backlog + AC | create, transition todo | — |
| `developer` | Implement | get, in_progress | `invoke_opencode` + **openprotocol-coder** |
| `qa` | Integrate | get, done | **openprotocol-integrator** (verify + squash merge) |

## Sequence

1. **PO** loads Axon context (optional), creates ticket with AC
2. **PO** transitions to `todo` + `assignee_agent_profile: developer`
3. **OpenTicket** webhooks **OpenOrchestrator** → dispatches Dev via `POST /v1/runs`
4. **Dev** loads `openprotocol-coder`, `get_ticket`, `invoke_opencode(mode=implement)`
5. **OpenCode** on `agent/<ticket>/…` branch → push → handoff comment → ticket `in_review`
6. **Orchestrator** assigns **QA**
7. **QA** loads `openprotocol-integrator` → verify branch → squash merge `main` → `done`
8. **OpenRec** receives `ticket.*` and `code.*` events

## Correlation IDs

Every ticket gets a `correlation_id`. Propagate through:
- Orchestrator task context
- OpenCode session (`X-Correlation-Id`)
- OpenRec RecEvent envelope

## Rules

- OpenOrchestrator **never** calls OpenCode directly
- Dev **never** merges to `main` — **openprotocol-coder** pushes branch only
- QA **never** skips verify — **openprotocol-integrator** squash-merges
- Dev **never** closes tickets to `done` — QA only
- Do not enable `/openagents true` inside OpenCode when invoked by OpenAgents

Company template:

```bash
/company init my-squad --template openpro-engineering
```
