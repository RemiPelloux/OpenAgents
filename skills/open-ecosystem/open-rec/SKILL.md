---
name: open-rec
description: "Emit and query RecEvents with correlation_id audit."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenRec, RecEvent, audit, W4, mesh]
    category: open-ecosystem
    related_skills: [open-contract, open-dev-workflow, open-ticket, open-brain-orchestrator]
---

# OpenRec (audit bus)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

**RecEvent** is the authoritative audit trail. Producers emit; OpenRec ingests (step 6).
Until ingest is live, producers use **outbox** + worker retry.

## When to Use

- Tracing what happened for ticket `OP-42` or correlation `corr-…`
- Implementing producer side effects (must emit RecEvent type)
- QA verifying W4 completed (ticket + code events received)

Do **not** use OpenRec as agent memory — Brain observations are searchable summaries; RecEvent is audit authority.

## Structural overview

| Piece | Role |
|-------|------|
| `event.type` | Declared by **producer** app |
| `correlation_id` | Same ID across ticket, OpenCode, orchestrator |
| `AuditEnvelope` | Tenant, actor, timestamp |
| API | `POST /v1/events` on `:3030` (when live) |
| Outbox | `outbox_jobs` + Rust worker until step 6 |

W4 types: `ticket.*`, `code.implementation.completed`, `agent.run.*`

## Prerequisites

- `OPENREC_API_URL` (default `http://localhost:3030` when running)
- Producer declares event types in app README + OpenRec registry
- `openos_engineering` plugin emits `agent.run.*` on `invoke_opencode`

## Procedure — producer

1. Declare RecEvent type in app scope before first emit
2. On state change, enqueue outbox job with `correlation_id`
3. Worker POSTs signed payload to OpenRec (or file outbox in dev)
4. Never block business transaction on RecEvent failure

## Procedure — query (agent)

1. Get `correlation_id` from ticket (`get_ticket`)
2. Query OpenRec read API or `ask_brain` with trace context
3. Confirm expected sequence: `agent.run.started` → `code.*` → `ticket.status_changed`

## Decision rules

| Situation | Action |
|-----------|--------|
| OpenRec not deployed yet | Outbox + worker; types still declared |
| Same action as Brain ingest | Both may fire — same `correlation_id`, different purpose |
| Missing correlation | Stop — fix producer before merging |

## Pitfalls

- Emitting undeclared event types
- Dropping `correlation_id` between hops
- Using RecEvent payload as RAG source without Brain ingest
- Blocking ticket transition on RecEvent POST failure

## Verification

- [ ] W4 E2E: `OpenTicket/scripts/w4-e2e.sh` shows RecEvent receipt (or outbox drained)
- [ ] `correlation_id` matches across ticket, OpenCode webhook, agent.run events
- [ ] Event type registered for owning producer app
