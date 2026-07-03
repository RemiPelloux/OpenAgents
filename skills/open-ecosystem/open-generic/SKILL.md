---
name: open-generic
description: "Default OpenOS agent loop when no skill fits."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openos, generic, fallback, agent-loop]
    category: open-ecosystem
    related_skills: [open-ecosystem-hub, open-brain, open-contract]
---

# Open Generic (OpenOS default loop)

Fallback discipline when no specialized skill matches — still follows mesh rules.

## When to Use

- Task spans OpenOS but hub routing is unclear
- One-off ops inside an app repo without a dedicated skill yet
- New vertical before a specialized skill exists

Load `open-ecosystem-hub` first — if a specific skill exists, **switch** to it.

## Prerequisites

- `open-ecosystem-hub` consulted
- `open-brain` `search_knowledge(domain=openos)` before guessing mesh behavior
- Know current profile (`developer`, `qa`, `product_owner`, …)

## Procedure (universal 8-step)

1. **Locate** — owning app repo; read README + neighbors
2. **Contract** — if hop touches another app → `open-contract` check
3. **Context** — `search_knowledge` / `get_ticket` as needed
4. **Make** — surgical change; Pelloux limits (≤400 LOC file, ≤25 LOC fn)
5. **Test** — app test + typecheck + build commands from README
6. **Review** — diff scope matches request only
7. **Ship** — OpenProtocol if git (`openprotocol-coder` / integrator)
8. **Observe** — RecEvent + Brain observation on meaningful state change

## Decision rules

| Signal | Escalate to |
|--------|-------------|
| Ticket + code | `open-dev-workflow` |
| CRM write | `opencrm-sales-followup` |
| Plan blocked | `open-orchestrator-ops` |
| New mesh edge | `open-mesh-wiring` |
| QA sign-off | `open-qa` |

## Pitfalls

- Staying on generic when a specialized skill exists
- Guessing CC-* IDs without Brain search
- Skipping tests because task feels small
- Inventing auth/tenant — use platform scoping only

## Verification

- [ ] Hub checked — no better skill ignored
- [ ] Tests/typecheck run for touched app
- [ ] Cross-app hops have contract + correlation_id
