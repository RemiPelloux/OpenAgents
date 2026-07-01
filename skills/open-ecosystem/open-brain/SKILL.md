---
name: open-brain
description: "Use OpenBrain (Axon) MCP Knowledge for OpenOS mesh docs and validated company knowledge — search_knowledge with domain openos."
version: 2.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openbrain, axon, mcp, knowledge, openos, rag]
    related_skills: [open-ecosystem-hub, open-memory, openagents]
---

# Open Brain (Axon)

**OpenBrain** is the OpenOS **Company Brain**: knowledge graph, review queue, Skills File, and MCP Knowledge server. For **internal OpenOS documentation** (contracts, specs, ADRs, app READMEs), use **`search_knowledge` with `domain: "openos"`** — not external OB1 templates.

## When to use

- User asks how OpenTicket, OpenContract, W4, or mesh integration works
- Before implementing cross-app features — query indexed docs first
- Validated institutional knowledge (graph nodes) vs raw markdown specs

Do **not** use for OpenAgents-only session memory — see `open-memory` first.

## OpenOS doc corpus

| Item | Value |
|------|-------|
| Domain filter | `openos` |
| Handbook | `OpenOS/docs/opencontract/OPENCONTRACT-HANDBOOK.md` |
| Sync | `OpenOS/scripts/brain-sync-docs.sh` |
| Web UI | OpenBrain Command Center → OpenOS Docs |

Example MCP call (Knowledge server):

```json
{
  "tool": "search_knowledge",
  "arguments": {
    "query": "What is CC-W4-005?",
    "domain": "openos",
    "app": "opencontract",
    "limit": 8
  }
}
```

Responses include **citations**: `{ path, title, chunkId, snippet, app }`.

## OpenAgents MCP setup

Add OpenBrain Knowledge MCP to `~/.openagents/config.yaml`:

```yaml
mcp_servers:
  openbrain-knowledge:
    url: ${OPENBRAIN_API_URL}/api/v1/mcp/knowledge
    headers:
      Authorization: Bearer ${AXON_AGENT_API_KEY}
```

Env: see [openos-mesh-env.md](../../docs/openos-mesh-env.md).

Reload after config change: `/mcp reload` or restart gateway.

## Graph knowledge vs doc RAG

| Surface | Use for |
|---------|---------|
| `search_knowledge` + `domain: openos` | Markdown/YAML specs, registry, ADRs |
| `search_knowledge` (no domain) | Validated graph nodes (processes, rules) |
| `get_skills_file` | Agent Skills File export |
| `ingest_content` | New content through connectors (human review path) |

## Agent usage patterns

1. **Query before guessing** — CC-* chains, envelope shape, E2E scripts
2. **Cite paths** — include `path` from citations in ticket/PR text
3. **OpenContract first** — handbook + registry for contract IDs
4. **Never store secrets** in brain ingest

## Sync cadence

- After clone: `./scripts/brain-sync-docs.sh --tier 0` (OpenOS root, OpenBrain up)
- After doc PR merge: `--app <AppFolder>` or full sync in CI/nightly
- Validate manifest: `./scripts/validate-brain-corpus.sh`

## Verification checklist

- [ ] MCP Knowledge responds to `tools/list`
- [ ] `search_knowledge` with `domain: openos` returns handbook hit for "CC-W4-005"
- [ ] Agent key can POST bulk ingest (sync script dry-run then real)
- [ ] OpenBrain `/openos-docs` UI returns same corpus

## Related

- [OpenBrain OPENOS-DOCS-RAG.md](https://github.com/RemiPelloux/OpenBrain/blob/main/docs/OPENOS-DOCS-RAG.md)
- [ADR-004 OpenOS docs via OpenBrain](https://github.com/RemiPelloux/OpenOS/blob/main/docs/adr/ADR-004-openos-docs-via-openbrain.md)
