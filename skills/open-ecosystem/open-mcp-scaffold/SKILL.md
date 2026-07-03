---
name: open-mcp-scaffold
description: "Add MCP tools with REST parity and CC-* first."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [mcp, scaffold, opencode, mesh]
    category: open-ecosystem
    related_skills: [open-contract, open-code, open-ecosystem-hub]
---

# Open MCP Scaffold

Close **capability gaps** — add missing MCP tools to OpenOS apps with mesh DoD.

## When to Use

- Orchestrator `capability_gap` in task context
- `invoke_opencode` ticket to add MCP tool to OpenCRM, OpenTicket, etc.
- New agent-facing action without MCP equivalent

## Prerequisites

- `open-contract` — register `CC-*` before code
- Target app hybrid layout: `apps/api` + `apps/mcp-server`
- OpenCode via `invoke_opencode` for implementation

## Procedure

1. Register `CC-*` in `OpenContract/registry/`
2. Add REST route in `apps/api` (Zod + OpenAPI)
3. Mirror in `apps/mcp-server/src/tools/` (`apiCall` pattern — copy OpenCRM)
4. Unit test + extend `scripts/*-e2e.sh`
5. Typecheck + test + build; session-complete if OpenCode owns the change
6. Mark contract `verified` after E2E

## Decision rules

| Gap type | Owner repo |
|----------|------------|
| OpenCRM tool | `OpenCRM/` |
| OpenTicket tool | `OpenTicket/` |
| Orchestrator tool | `OpenOrchestrator/` |

## Pitfalls

- MCP without REST parity
- Skipping registry row (step 0)
- Agent tool in Rust MCP (use TS MCP per polyglot ADR)

## Verification

- [ ] `tools/list` on MCP server includes new tool
- [ ] REST endpoint matches MCP behavior
- [ ] E2E script covers happy + error path
