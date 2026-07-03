---
name: openprotocol-coder
description: "Branch, code via OpenCode, push, handoff to QA."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenProtocol, Git, OpenCode, developer]
    category: open-ecosystem
    related_skills: [open-code, open-ticket, openprotocol-integrator, open-dev-workflow]
---

# OpenProtocol — Coder

**Developer** role: OpenAgents spawns OpenCode; you own the feature branch until handoff.

## When to Use

- `developer` profile on a code ticket
- Before `invoke_opencode(mode=implement)`
- Any git write except merge to `main`

## Prerequisites

- `GITHUB_TOKEN` in `~/.openagents/.env` (SSM on AWS) or `~/.git-credentials`
- Git `user.name` / `user.email` configured on host
- Target repo cloned with `origin` remote

## Procedure

1. `git fetch && git checkout main && git pull --ff-only`
2. `git checkout -b agent/<ticket-key>/<short-slug>`
3. `invoke_opencode(ticket_id, mode=implement, cwd=<app-repo>)`
4. Verify: project test + typecheck + build
5. Commit if needed: `<type>(<scope>): <subject>`
6. `git push -u origin HEAD`
7. `add_ticket_comment` handoff block → `in_review`

## Handoff template

```
OpenProtocol handoff
- Branch: agent/<key>/<slug>
- Repo: <path>
- Checks: test ✓ | typecheck ✓ | build ✓
- Risk: low|medium|high — <line>
```

## Decision rules

| Situation | Action |
|-----------|--------|
| OpenOS submodule | Branch inside app repo |
| Push fails | Report error — do not merge locally to main |
| User says don't push | Skip push; still run tests |

## Pitfalls

- Merging to `main` from coder session
- Missing handoff (QA blocked)
- Wrong repo `cwd` for submodule work

## Verification

- [ ] Branch prefix `agent/`
- [ ] Remote branch exists after push
- [ ] Ticket comment contains handoff block
