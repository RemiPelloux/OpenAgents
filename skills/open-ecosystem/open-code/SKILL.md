---
name: open-code
description: "Spawn OpenOS OpenCode via invoke_opencode tool."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCode, W4, invoke_opencode]
    category: open-ecosystem
    related_skills: [openprotocol-coder, open-ticket, open-dev-workflow, open-contract]
---

# OpenOS OpenCode

OpenAgents delegates all code edits to the **OpenOS OpenCode fork** — not npm `opencode-ai`.

Follow `open-ecosystem/OPERATING-STANCE.md`. Launch OpenCode in this turn when the work is code.

## When to Use

- `developer` or `qa` profile needs implement / review / test on a ticket
- `invoke_opencode` plugin tool is available
- W4 coding step after `get_ticket`

Load **`openprotocol-coder`** before implement; QA merge uses **`openprotocol-integrator`** (git on host, not inside OpenCode).

## Prerequisites

- Plugin `openos_engineering` enabled
- `OPENOS_OPENCODE_PATH` or `opencode` on PATH
- `OPENTICKET_API_URL` + ticket id
- `OPENCODE_INVOKED_BY=openagents` set by plugin (do not override)

## Procedure

```
invoke_opencode(ticket_id="OP-42", mode="implement", cwd="/path/to/app/repo")
# Or loop until DoD:
run_ticket_dod_loop(ticket_id="OP-42", agent_profile="developer", cwd="/path/to/app/repo")
```

| Mode | Actor | Purpose |
|------|-------|---------|
| `implement` | developer | Build to AC on `agent/…` branch |
| `review` | qa | Diff vs AC |
| `test` | qa | Run test suite |

Plugin flow: fetch ticket → set env → headless `-p --bare` → session-complete webhook (`CC-W4-005`).

## Decision rules

| Situation | Action |
|-----------|--------|
| Plugin missing | Fallback CLI with `OPENTICKET_TICKET_ID` env |
| Inside OpenCode session | Never `/openagents true` |
| Submodule change | `cwd` = app repo, not OpenOS root only |

## Pitfalls

- Using bundled `opencode` skill (generic CLI) instead of this OpenOS fork
- Wrong `cwd` (OpenOS root vs `OpenCode/` submodule)
- Expecting OpenCode to merge `main` (integrator does that)

## Verification

- [ ] `verify_opencode_binary()` or `opencode --version` succeeds
- [ ] Session sets `OPENCODE_INVOKED_BY=openagents`
- [ ] Ticket reaches `in_review` after implement (webhook or manual comment)
- [ ] `CC-W4-005` envelope on session-complete when strict signing on
