---
name: openprotocol-integrator
description: "OpenProtocol integrator: verify branch, test, squash merge."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenProtocol, Git, QA, OpenAgents, W4]
    category: open-ecosystem
    related_skills: [openprotocol-coder, open-dev-workflow, open-ticket]
---

# OpenProtocol — Integrator (OpenAgents QA)

You are the **Integrator** role. A coder agent (OpenCode via `invoke_opencode`)
pushed an `agent/…` branch. You verify, merge to `main`, clean up.

**Never** rewrite coder commits. **Never** merge with failing tests.

## When to Use

- `qa` profile after ticket is `in_review`
- Handoff comment contains `OpenProtocol handoff` + branch name
- OpenOrchestrator assigned integration / sign-off step

## Prerequisites

- Same git auth as coder: `GITHUB_TOKEN` in `~/.openagents/.env` or git-credentials
- Branch name from ticket comment or handoff block
- **Do not** use interactive GitHub CLI login

## Procedure

### 1. Read handoff

`get_ticket` → find branch in comments (`Branch: agent/...`).

### 2. Fetch and checkout

```bash
terminal(command="git fetch origin && git checkout <branch> && git pull --ff-only origin <branch>")
```

### 3. Full verify (do not trust handoff alone)

Run project DoD — example OpenCode app:

```bash
terminal(command="bun test && bunx tsc --noEmit && bun run install:local-bin", workdir="<app-repo>")
```

Review diff:

```bash
terminal(command="git log origin/main..HEAD --oneline && git diff origin/main...HEAD --stat")
```

**Reject** if: failing tests, secrets in diff, scope creep, bad commit format.
Comment on ticket; send back to developer on the **same branch**.

### 4. Squash merge to `main`

```bash
terminal(command="git checkout main && git pull --ff-only origin main")
terminal(command="git merge --squash <branch>")
terminal(command="git commit -m \"<type>(<scope>): <subject>

Integrates agent/<key>/<slug>. Verified: test, typecheck, build.\"")
terminal(command="git push origin main")
```

Never `git push --force` to `main`.

### 5. Cleanup

```bash
terminal(command="git push origin --delete <branch> && git branch -d <branch>")
```

OpenOS meta repo: if submodule changed, bump pointer at OpenOS root in a
separate commit (`chore: bump <App> submodule`).

### 6. Close ticket

`update_ticket_status` → `done` (QA only). `add_ticket_comment` with merge SHA.

Optional: `invoke_opencode(mode=test)` before merge if extra validation needed.

## OpenOS submodule matrix

| Change in | Integrate in | Then |
|-----------|----------------|------|
| `OpenCode/` only | OpenCode repo | done |
| App + OpenOS pointer | app repo first | bump submodule at OpenOS root |

## Pitfalls

- Merging without re-running tests
- Force-push to `main`
- Deleting branch before merge confirmed on remote

## Verification

- `main` on origin contains squash commit
- Feature branch deleted on origin
- Ticket status `done` with merge note
