---
name: open-mcp-scaffold
description: Scaffold missing OpenOS MCP tools via OpenCode.
version: "1.0.0"
author: OpenOS
metadata:
  hermes:
    category: open-ecosystem
    tags: [openos, mcp, opencode]
---

# Open MCP Scaffold Skill

Use when OpenOrchestrator reports a **capability gap** — a required MCP tool missing from the mesh catalog.

## When to Use

- Task context includes `capability_gap` with `tool` and `app`
- `invoke_opencode` ticket asks to add an MCP tool to OpenCRM, OpenTicket, or another OpenOS app

## Procedure

1. Register `CC-*` in `OpenContract/registry/` before coding.
2. Add REST route in `apps/api` if missing.
3. Mirror tool in `apps/mcp-server/src/tools/` using Zod + `apiCall` pattern (copy OpenCRM `read-tools.ts`).
4. Add unit test + extend app `scripts/*-e2e.sh`.
5. Run type-check and tests; session-complete webhook must fire.

## Verification

- `tools/list` on the app MCP server includes the new tool name
- OpenOrchestrator deploy webhook closes the skill gap
