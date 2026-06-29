---
name: open-brain
description: "Use when setting up or integrating Open Brain — shared SQL+vector memory with MCP for Claude, Cursor, OpenAgents, and other AI tools."
version: 1.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openbrain, memory, mcp, pgvector, postgres, sqlite, embeddings]
    homepage: https://github.com/NateBJones-Projects/OB1
    related_skills: [open-ecosystem-hub, open-memory, openagents]
---

# Open Brain

**Open Brain** is persistent, tool-agnostic memory infrastructure: raw content and embeddings stored separately so you can rebuild indexes when embedding models change. Any MCP-compatible client (Cursor, Claude Desktop, OpenAgents, etc.) can share one knowledge base.

> Reference implementation: [NateBJones-Projects/OB1](https://github.com/NateBJones-Projects/OB1) (also arpdale/open-brain templates).

## When to use

- User wants **one memory** across multiple AI tools
- Setting up Postgres/SQLite + pgvector + MCP memory server
- Capturing thoughts from Slack/Discord into a searchable store
- Integrating OpenAgents with an external memory layer (with `open-memory`)

Do **not** use for OpenAgents-only built-in memory — see `open-memory` first.

## Core concepts

| Concept | Meaning |
|---------|---------|
| Source table | Canonical text/metadata — survives embedding model upgrades |
| Embedding index | Derived vectors — rebuild without losing source data |
| MCP server | Exposes recall/write tools to agents |
| Capture channel | Slack, Discord, or manual ingest into the brain |

## Setup path (high level)

1. **Database** — Postgres + pgvector (production) or SQLite (local dev)
2. **MCP server** — configure executable + env in each AI client
3. **Optional capture** — Slack/Discord bots for quick capture
4. **Dashboard** — optional SvelteKit/Next.js UIs from OB1 community dashboards

Follow upstream docs: `docs/01-getting-started.md` in the OB1 repo (~45 min full setup).

## OpenAgents integration

Add MCP server to `~/.openagents/config.yaml`:

```yaml
mcp_servers:
  open-brain:
    command: npx
    args: ["-y", "@your-scope/open-brain-mcp"]
    env:
      DATABASE_URL: postgresql://...
```

Then enable MCP toolset: `openagents tools enable mcp` (or include in profile toolsets).

Reload MCP after config change: `/mcp reload` in CLI or restart gateway.

## Agent usage patterns

- **Recall before acting** — query Open Brain for user preferences, project facts, past decisions
- **Write after learning** — store durable facts the user confirms (not ephemeral chat)
- **Tag by domain** — hiring (`open-pro`), compliance (`open-whistle`), infra — aids retrieval
- **Never store secrets** — API keys, whistleblower PINs, or raw PII belong elsewhere

## Rebuild embeddings (model upgrade)

Because source and embeddings are decoupled:

1. Deploy new embedding model config
2. Run rebuild job against source rows only
3. Validate recall quality on a sample set
4. Cut over MCP server config

## Common pitfalls

1. **Chunk-only vector stores** — loses rebuild flexibility; Open Brain keeps SQL source of truth
2. **Duplicating memory** — also using Honcho + Open Brain without scope rules causes conflicting facts
3. **Over-writing** — agents appending noise; use confirmation for high-impact memories
4. **Missing MCP reload** — OpenAgents won't see new tools until reload/restart

## Verification checklist

- [ ] MCP server responds to `tools/list` from OpenAgents
- [ ] Test recall returns expected snippet for a seeded memory
- [ ] Write path stores in source table (inspect SQL)
- [ ] Embedding rebuild documented if model version changed
- [ ] PII/compliance boundaries agreed with `open-whistle` / `open-pro` if shared DB
