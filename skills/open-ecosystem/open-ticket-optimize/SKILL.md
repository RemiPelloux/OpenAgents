---
name: open-ticket-optimize
description: "Optimize tickets: AC, sizing, backlog hygiene."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenTicket, PO, backlog, AC, grooming]
    category: open-ecosystem
    related_skills: [open-ticket, open-dev-workflow, open-brain, open-orchestrator-plan]
---

# Open Ticket Optimize

**Product Owner** discipline: sharp tickets agents can execute without guesswork.

## When to Use

- Backlog grooming before sprint/mission
- Ticket rejected by Dev/QA for vague AC
- Splitting epics into agent-sized stories
- Adding correlation_id, labels, ETA, execution_mode

Not for implementation — PO and planner profiles only.

## Prerequisites

- OpenTicket MCP installed
- Optional: `open-brain` for spec citations in description
- `open-orchestrator-plan` when objective needs decomposition first

## Structural overview

| Field | Agent needs |
|-------|-------------|
| `title` | Imperative, ≤72 chars |
| `acceptance_criteria[]` | Testable, numbered, no jargon |
| `description` | Context + links to Brain paths |
| `assignee_agent_profile` | `developer` \| `qa` \| `researcher` |
| `execution_mode` | `code` \| `research` |
| `correlation_id` | Propagates to W4 + OpenRec |
| `labels` | `engineering`, `security`, `sales`, … |

## Procedure — optimize one ticket

1. `get_ticket` or draft new
2. **Title** — one outcome verb ("Add webhook retry to ticket worker")
3. **AC** — Given/When/Then or checklist; each item independently testable
4. **Size** — if >5 AC or >3 apps → split into linked tickets
5. **Cite** — paste `open-brain` paths for CC-* or spec references
6. **Profile** — `developer` for code; `researcher` for non-code tasks
7. **ETA** — set via `set_ticket_eta` or priority default
8. `update_ticket` / `create_ticket` → `todo` when ready

## Procedure — backlog sweep

1. `list_tickets(status=backlog)` or `search_tickets`
2. Drop duplicates; merge AC from scattered comments
3. Close stale without AC — or fast-fill AC before `todo`
4. Order by priority; assign `correlation_id` per mission batch

## Decision rules

| Ticket smells | Fix |
|---------------|-----|
| "Improve X" | Measurable AC with metric |
| AC in prose paragraph | Split to `acceptance_criteria[]` array |
| Multi-repo epic | Parent + child tickets with shared `correlation_id` |
| Research task | `execution_mode: research`, not `invoke_opencode` |

## Pitfalls

- AC that agents cannot verify ("make it better")
- Missing `assignee_agent_profile` on `todo` transition
- Code ticket without Brain/spec citation for mesh work
- 20 AC on one ticket — agents lose focus

## Verification

- [ ] Each AC is boolean-testable by QA (`open-qa` checklist)
- [ ] Dev can start with `get_ticket` only — no slack DMs needed
- [ ] `correlation_id` present before `todo`
- [ ] Ticket fits one branch (`agent/<key>/…`) for code work
