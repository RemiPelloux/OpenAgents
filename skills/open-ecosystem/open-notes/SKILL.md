---
name: open-notes
description: "Meeting audio intel; observations to Brain and CRM."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenNotes, meetings, Brain, CRM, W1]
    category: open-ecosystem
    related_skills: [open-brain, opencrm-sales-followup, open-ticket]
---

# OpenNotes (meeting intelligence)

Captures meetings/audio → structured notes → Brain observations + optional CRM/ticket hooks.

## When to Use

- User asks to process a meeting recording or transcript
- PO wants ticket AC derived from meeting notes
- CRM follow-up needs meeting context (with `opencrm-sales-followup`)

Do **not** store raw audio in agent memory — use OpenNotes APIs and Brain ingest summaries.

## Structural overview

| Output | Channel |
|--------|---------|
| Summary observation | Brain channel B (`POST …/brain/observations`) |
| CRM entity hints | OpenCRM via W1 contracts |
| Ticket draft | OpenTicket `create_ticket` (PO profile) |

Contracts: W1 family (`CC-W1-*`) — query `open-brain` `domain: openos` before implementing.

## Prerequisites

- OpenNotes API running (see `OpenNotes/README.md`)
- Brain: `OPENBRAIN_API_URL` + `AXON_AGENT_API_KEY`
- Optional: `OPENCRM_API_URL` for commercial linkage

## Procedure

1. **Query** `open-brain` for W1 contract + payload shape
2. **Ingest** meeting via OpenNotes REST/MCP (app-specific tools)
3. **Emit** Brain observation (`sourceType: event`, title + summary, `correlation_id`)
4. **If** commercial action needed → hand off to `opencrm-sales-followup` or PO ticket

## Decision rules

| Situation | Action |
|-----------|--------|
| Code change requested | PO creates ticket → W4 (`open-dev-workflow`) |
| Sales follow-up only | `opencrm-sales-followup` staged update |
| Doc/spec question | `open-brain` `search_knowledge` first |

## Pitfalls

- PII in observation `content` — summarize, redact secrets
- Skipping Brain ingest on meaningful state changes
- Bypassing OpenContract on OpenNotes → CRM hops

## Verification

- [ ] Observation returns `observationId` (idempotent)
- [ ] `search_observations(app=opennotes)` finds the summary
- [ ] `correlation_id` present when tied to a ticket/mission
