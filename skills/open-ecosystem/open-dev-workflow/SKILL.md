---
name: open-dev-workflow
description: "W4: PO ticket → Dev OpenCode → QA integrator."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [W4, workflow, OpenTicket, OpenCode]
    category: open-ecosystem
    related_skills: [open-ticket, open-code, openprotocol-coder, open-qa, openprotocol-integrator, open-rec, open-contract, open-ticket-optimize, open-orchestrator-ops]
---

# Open Dev Workflow (W4)

End-to-end engineering loop with **OpenAgents** as runtime and **OpenCode** as coder.

## When to Use

- Any ticket-backed implementation, review, or release
- Orchestrator dispatched `developer` or `qa` profile
- User mentions W4, session-complete, or agent branch merge

## Prerequisites

```bash
openagents openos init-profiles
```

| Service | URL |
|---------|-----|
| OpenTicket | `http://localhost:3020` |
| OpenOrchestrator | `http://localhost:3050` |
| OpenCode | `OPENOS_OPENCODE_PATH` or built binary |
| Git auth | `GITHUB_TOKEN` in `~/.openagents/.env` |

## Procedure

1. **PO** — `open-ticket-optimize` → `create_ticket` + AC → `todo` → `assignee_agent_profile: developer`
2. **Webhook** — OpenTicket → OpenOrchestrator → `POST /v1/runs` (Dev) with `loop_until_dod: true`
3. **Dev** — load `openprotocol-coder` → `run_ticket_dod_loop` or `invoke_opencode` until `in_review`
4. **OpenCode** — branch `agent/<ticket>/…` → push → handoff → `in_review` (may take multiple sessions)
5. **Orchestrator** — assign QA; QA loops review/test until ticket `done`
6. **QA** — load `open-qa` → AC + tests → `openprotocol-integrator` → squash merge `main` → `done`
7. **Audit** — OpenRec receives `ticket.*`, `code.*`, `agent.run.*` (see `open-rec`)

## Decision rules

| Role | May | Must not |
|------|-----|----------|
| Developer | push feature branch | merge `main` |
| QA | squash merge after `open-qa` sign-off | skip tests |
| Orchestrator | dispatch OpenAgents | call OpenCode directly |
| OpenCode | implement on branch | enable `/openagents true` |

## Pitfalls

- Missing `correlation_id` on webhooks
- Dev closing ticket to `done`
- OpenCode loop via `/openagents true`
- Skipping `open-contract` on new session-complete hops

## Verification

- [ ] Ticket path: `todo` → `in_progress` → `in_review` → `done`
- [ ] Branch `agent/*` exists on remote before QA merge
- [ ] W4 E2E: `OpenTicket/scripts/w4-e2e.sh` green (or outbox drained)
- [ ] `CC-W4-003`..`005` envelopes verified in registry
