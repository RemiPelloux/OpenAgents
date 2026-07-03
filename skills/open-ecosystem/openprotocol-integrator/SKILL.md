---
name: openprotocol-integrator
description: "Verify agent branch, squash merge main, cleanup."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenProtocol, Git, QA, integrator]
    category: open-ecosystem
    related_skills: [openprotocol-coder, open-ticket, open-dev-workflow, open-rec]
---

# OpenProtocol — Integrator

**QA** role: verify coder branch, merge to `main`, delete branch, close ticket.

## When to Use

- Ticket `in_review` with `OpenProtocol handoff` comment
- QA profile assigned by orchestrator
- After coder pushed `agent/…` branch

## Prerequisites

- Same `GITHUB_TOKEN` / git auth as coder host
- Branch name from ticket comment
- Full test suite commands for target app

## Procedure

1. `get_ticket` — parse branch from handoff
2. `git fetch && git checkout <branch> && git pull --ff-only`
3. Run full DoD (test, typecheck, build)
4. `git diff origin/main...HEAD` — reject if scope creep or secrets
5. `git checkout main && git pull --ff-only`
6. `git merge --squash <branch>` → commit → `git push origin main`
7. `git push origin --delete <branch>`
8. `update_ticket_status` → `done` + merge SHA comment

## Decision rules

| Check fails | Action |
|-------------|--------|
| Tests red | Comment on ticket; same branch fix by Dev |
| Secrets in diff | Reject; never merge |
| Submodule + meta | Merge app first; bump OpenOS pointer separately |

## Pitfalls

- Trusting handoff without re-running tests
- Force-push to `main`
- `done` before remote merge confirmed

## Verification

- [ ] Squash commit on `origin/main`
- [ ] Feature branch deleted on origin
- [ ] Ticket `done` with merge note
