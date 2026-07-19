---
name: open-ecosystem-hub
description: "Route OpenOS tasks to the correct product skill."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openos, ecosystem, routing, mesh]
    category: open-ecosystem
    related_skills: [open-dev-workflow, open-contract, open-brain, open-ticket]
---

# Open Ecosystem Hub

Router for the **OpenOS mesh**. Load this when the user names "Open" products without picking one, or when a task spans multiple apps.

## When to Use

- Ambiguous "Open" request — pick the owning skill first
- Cross-app workflow (ticket + code + audit + contract)
- Onboarding an agent to the suite

Do **not** stop at this hub — always load the **specific** skill next.

## Prerequisites

- Know which OpenOS app owns the request (use routing table below)
- For mesh hops: `open-contract` before any new integration

## Procedure

1. Read user intent — code, ticket, audit, CRM, compliance, or UI?
2. Pick skill from routing table
3. Load that skill fully before acting
4. If cross-app: plan `correlation_id` + contract IDs via `open-brain`

## Structural overview

```
OpenContract (step 0) → producers (Ticket, CRM, Notes, Team, Sec)
  → OpenAgents (runtime) → OpenCode (code) → OpenRec (audit)
  → OpenBrain (RAG) → OpenOrchestrator (control plane)
  → OpenCenter (human GUI, Phase 2)
```

## Routing table

| Need | Skill |
|------|-------|
| CC-* / envelopes | `open-contract` |
| W4 ticket → code → merge | `open-dev-workflow` + `openprotocol-coder` + `open-qa` + `openprotocol-integrator` |
| Tickets / backlog | `open-ticket` |
| Ticket Prompt Optimizer / AC | `open-ticket-optimize` |
| Headless coding | `open-code` |
| Mesh integration (new hop) | `open-mesh-wiring` |
| No matching skill | `open-generic` |
| Audit / correlation trace | `open-rec` |
| Doc RAG / missions | `open-brain` / `open-brain-orchestrator` |
| Plans / dispatch | `open-orchestrator-plan` / `open-orchestrator-intent` |
| Industrial orchestration | `open-orchestrator-ops` |
| QA sign-off (before merge) | `open-qa` |
| CRM sales | `opencrm-sales-followup` |
| CRM lead enrichment | `opencrm-contact-enrichment` |
| TikTok prospection | `openpro-tiktok-prospection` |
| OpenTeam harvest | `open-team` |
| Meetings | `open-notes` |
| Security findings | `open-sec` |
| Whistleblower | `open-whistle` |
| Agent memory | `open-memory` |
| Workflows YAML | `open-agentui` |
| Human GUI (later) | `open-center` |
| MCP tool gap | `open-mcp-scaffold` |
| OpenAgents runtime | bundled `openagents` skill |

## Decision rules

| If user wants… | Then load… |
|----------------|------------|
| Code on a ticket | `open-dev-workflow` → `open-code` → `openprotocol-coder` |
| Optimize / create tickets | `open-ticket-optimize` → `open-ticket` |
| Wire new mesh edge | `open-mesh-wiring` → `open-contract` |
| Orchestrator gates / DLQ | `open-orchestrator-ops` |
| Verify before merge | `open-qa` → `openprotocol-integrator` |
| Unsure which skill | `open-generic` (then hub again) |
| "What is CC-W4-005?" | `open-brain` (`domain: openos`) |
| New API tool in OpenCRM | `open-contract` → `open-mcp-scaffold` |
| Merge agent branch | `openprotocol-integrator` (QA) |
| Mobile hiring app | `open-pro` (OpenPro Core — outside umbrella) |

## Pitfalls

- Using npm `opencode` skill instead of `open-code` (OpenOS fork)
- Enabling `/openagents true` inside spawned OpenCode (loop)
- Skipping OpenContract on new mesh edges
- Building per-app dashboards before OpenCenter Phase 2

## Verification

- [ ] Named the owning app and loaded its skill (not hub only)
- [ ] Cross-app flow has `correlation_id` plan
- [ ] PII/compliance boundaries respected (`open-whistle` vs `open-memory`)
