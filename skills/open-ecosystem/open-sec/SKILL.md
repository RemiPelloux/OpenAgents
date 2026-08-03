---
name: open-sec
description: "Triage findings; bridge OpenSec to OpenTicket."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenSec, security, W3, findings, mesh]
    category: open-ecosystem
    related_skills: [open-ticket, open-contract, open-rec, open-brain]
---

# OpenSec (security command center)

Aggregates scanner findings → tickets → agent remediation (W3 mesh).

## When to Use

- Security profile triages a finding
- User asks to open a ticket from a CVE/scan result
- W3 workflow: finding → ticket → dev fix

Sensitive compliance intake requires an explicitly authorized workflow with
dedicated retention and disclosure controls; it is not stored as ordinary agent
memory.

## Structural overview

| Piece | Port | Role |
|-------|------|------|
| OpenSec API | TBD | Findings CRUD, severity |
| OpenTicket bridge | `CC-W3-*` | Finding → ticket |
| OpenRec | `rec_event` | `finding.*` audit |

Query contracts: `search_knowledge(domain=openos, query=CC-W3)`.

## Prerequisites

- `OPENSEC_API_URL` when API is running
- `OPENTICKET_API_URL` + MCP `openticket` for ticket bridge
- Profile: `security` (PO-style ticket create)

## Procedure

1. `search_knowledge` for W3 contract IDs and payload shapes
2. Fetch finding from OpenSec API (REST/MCP when available)
3. `create_ticket` with `type: bug`, AC = remediation steps, labels `security`
4. Assign `developer` → W4 (`open-dev-workflow`)
5. Emit RecEvent on state changes

## Decision rules

| Severity | Action |
|----------|--------|
| critical/high | Ticket priority high; `risk_level` ≥ 3 on orchestrator goal |
| low/info | Batch or backlog per policy |
| False positive | Comment + close in OpenSec, no ticket |

## Pitfalls

- Storing exploit details in Brain without redaction
- Skipping OpenContract on finding → ticket hop
- Dev merging security fix without QA on integrator path

## Verification

- [ ] Ticket links `finding_id` in metadata
- [ ] `correlation_id` spans finding → ticket → fix
- [ ] RecEvent `finding.triaged` or equivalent emitted
