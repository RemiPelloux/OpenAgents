---
name: open-brain
description: "Axon MCP: search OpenOS docs and graph knowledge."
version: 2.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openbrain, axon, mcp, rag, openos]
    category: open-ecosystem
    related_skills: [open-contract, open-ecosystem-hub, open-memory, open-brain-orchestrator, openbrain-chat-mermaid]
---

# Open Brain (Axon)

Company Brain: graph, doc RAG, Skills File, MCP Knowledge.

## When to Use

- "How does CC-W4-005 work?" — before guessing mesh behavior
- Cross-app feature — query indexed OpenOS docs first
- Validated institutional knowledge vs raw specs

For session-local recall only → `open-memory` first.

## Prerequisites

```yaml
# ~/.openagents/config.yaml
mcp_servers:
  openbrain-knowledge:
    url: ${OPENBRAIN_API_URL}/api/v1/mcp/knowledge
    headers:
      Authorization: Bearer ${AXON_AGENT_API_KEY}
```

Env: `OPENBRAIN_API_URL`, `AXON_AGENT_API_KEY` — see `OpenAgents/docs/openos-mesh-env.md`.

## Structural overview

| Surface | Use for |
|---------|---------|
| `search_knowledge` + `domain: openos` | Specs, ADRs, registry, READMEs |
| `search_knowledge` (no domain) | Validated graph nodes |
| `search_observations` + `app` | Runtime what-happened |
| `get_skills_file` | Agent skills export |
| `ingest_observation` | Channel B summaries (non-blocking) |

## Procedure

1. `search_knowledge(query, domain="openos", app=optional, limit=8)`
2. Cite `path` from hits in tickets/PRs
3. For contracts → `open-contract` handbook + registry paths from citations
4. After doc edits → `brain-sync-docs.sh` (maintainer)

Example:

```json
{"query": "CC-W4-005 envelope", "domain": "openos", "app": "opencode", "limit": 8}
```

## Decision rules

| Question type | Tool |
|---------------|------|
| Mesh spec / CC-* | `search_knowledge` domain openos |
| What happened in app | `search_observations` |
| Store new runtime fact | `ingest_observation` (no secrets/PII) |

## Pitfalls

- Guessing contract IDs without search
- Storing secrets in observations
- Using Brain as RecEvent authority (use `open-rec` for audit)

## Verification

- [ ] MCP `tools/list` includes `search_knowledge`
- [ ] Query `CC-W4-005` returns handbook citation
- [ ] Agent key can run sync dry-run when maintaining docs
