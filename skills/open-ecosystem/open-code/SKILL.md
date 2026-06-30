---
name: open-code
description: "Delegate coding to OpenOS OpenCode fork via invoke_opencode plugin tool (W4 ticket workflow)."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenCode, OpenOS, Coding, W4, OpenTicket]
    related_skills: [open-ticket, open-dev-workflow, opencode]
---

# OpenOS OpenCode (Engineering Co-pilot)

Use the **OpenOS OpenCode fork** (not npm `opencode-ai`) for ticket-backed coding via the `invoke_opencode` plugin tool.

## When to Use

- Developer or QA profile working on an OpenTicket issue
- User asks to implement, review, or test a ticket with OpenCode
- W4 workflow: Dev delegates all code changes to OpenCode

## Prerequisites

- `openos-engineering` plugin enabled (bundled)
- OpenCode binary: set `OPENOS_OPENCODE_PATH` or build from `OpenCode/`
- OpenTicket API at `OPENTICKET_API_URL` (default `http://localhost:3020`)

## Primary path: invoke_opencode tool

```
invoke_opencode(ticket_id="OP-42", mode="implement", cwd="/path/to/repo")
```

Modes:
- `implement` — Dev implements acceptance criteria
- `review` — QA reviews diff against AC
- `test` — QA runs tests

The tool automatically:
1. Fetches ticket + acceptance criteria from OpenTicket
2. Sets `OPENTICKET_TICKET_ID` and `OPENCODE_INVOKED_BY=openagents`
3. Runs OpenCode headless (`-p --bare`)
4. OpenCode emits session-complete webhook → ticket moves to `in_review`

## Do NOT enable OpenAgents inside OpenCode

When OpenAgents invokes OpenCode, **do not** run `/openagents true` in OpenCode — that reverses the flow and causes loops.

## Fallback: manual headless CLI

If the plugin tool is unavailable:

```bash
export OPENCODE_INVOKED_BY=openagents
export OPENTICKET_TICKET_ID=<uuid>
opencode -p --bare --max-turns 50 "Implement OP-42: ..."
```

## Binary resolution order

1. `$OPENOS_OPENCODE_PATH`
2. `which opencode` (compiled OpenOS binary)
3. `bun $OPENOS_ROOT/OpenCode/entrypoints/cli.tsx`
