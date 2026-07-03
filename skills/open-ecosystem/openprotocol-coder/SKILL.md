---
name: openprotocol-coder
description: "OpenProtocol coder: branch, verify, commit, push, handoff."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenProtocol, Git, OpenCode, OpenAgents, W4]
    category: open-ecosystem
    related_skills: [open-code, open-dev-workflow, openprotocol-integrator]
---

# OpenProtocol — Coder (OpenAgents → OpenCode)

You are the **Coder** role in OpenProtocol. OpenAgents spawns **OpenCode**
(`invoke_opencode`) to edit the repo. You never merge to `main`.

## When to Use

- `developer` profile implements a ticket
- `invoke_opencode(mode=implement)` is about to run
- Any delegated coding task that must land on a feature branch

## Prerequisites

- Git repo with `origin` pointing at GitHub
- **Machine auth** — one of:
  - `GITHUB_TOKEN` in `~/.openagents/.env` (AWS/headless — preferred in prod)
  - `~/.git-credentials` with `x-access-token` (deploy PAT)
  - SSH deploy key in `~/.ssh/` + `git@github.com:` remote
- OpenCode binary: `OPENOS_OPENCODE_PATH` or `opencode` on PATH
- **Do not** use interactive GitHub CLI login — not available on headless hosts

### Verify auth (before push)

```bash
terminal(command="test -n \"$GITHUB_TOKEN\" && curl -sf -H \"Authorization: token $GITHUB_TOKEN\" https://api.github.com/user | head -c 200 || git ls-remote origin HEAD")
```

## Procedure

### 1. Branch (never work on `main`)

```bash
terminal(command="git fetch origin && git checkout main && git pull --ff-only origin main")
terminal(command="git checkout -b agent/<ticket-key>/<short-slug>")
```

Examples: `agent/OP-42/session-artifacts`, `agent/W4-012/fix-webhook`

OpenOS submodules: `cd` into the **app repo** that owns the change before branching.

### 2. Delegate coding to OpenCode

```
invoke_opencode(ticket_id="OP-42", mode="implement", cwd="/path/to/app/repo")
```

OpenCode must: locate → make (Pelloux: surgical, file ≤400 LOC) → run tests/typecheck/build.

### 3. After OpenCode returns — verify locally if needed

Run the app’s DoD commands (OpenCode repo: `bun test`, `bunx tsc --noEmit`).

### 4. Commit (if OpenCode did not commit)

```
<type>(<scope>): <imperative subject ≤ 72 chars>
```

Types: `feat|fix|refactor|test|docs|chore|ci|perf` — one logical change per commit.

### 5. Push branch only

```bash
terminal(command="git push -u origin HEAD")
```

Never push `main`. Never `--force` on shared branches.

### 6. Handoff (mandatory — ticket comment)

Post via `add_ticket_comment` or `submit_ticket_result`:

```
OpenProtocol handoff
- Branch: agent/<ticket-key>/<slug>
- Repo: <path or owner/repo>
- Checks: test ✓ | typecheck ✓ | build ✓
- Risk: low|medium|high — <one line>
- Integrator: openprotocol-integrator on this branch
```

Transition ticket to `in_review`. **Stop** — do not merge.

## Pitfalls

- Merging to `main` from the coder session
- Skipping handoff — integrator agent cannot find the branch
- Using personal interactive GitHub CLI login on AWS/EC2 (fails headless)
- Cross-app changes in one commit — one repo per commit; contract first

## Verification

- `git branch --show-current` starts with `agent/`
- `git log origin/main..HEAD` shows only scoped commits
- Remote branch exists: `git ls-remote origin agent/...`
