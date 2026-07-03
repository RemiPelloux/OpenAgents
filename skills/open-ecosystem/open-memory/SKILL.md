---
name: open-memory
description: "OpenAgents session memory, Honcho, Brain bridge."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [memory, openagents, honcho, session]
    category: open-ecosystem
    related_skills: [open-brain, open-ecosystem-hub]
---

# Open Memory

How **OpenAgents** remembers within/across sessions — distinct from Open Brain.

## When to Use

- Tune memory mode (`hybrid` / `honcho` / `local`)
- Debug "agent forgot X"
- Decide OpenAgents vs Open Brain storage

## Structural overview

| Store | Scope |
|-------|-------|
| `~/.openagents/` memory files | Per profile session |
| `sessions.db` | Conversation FTS |
| Honcho plugin | External dialectic memory |
| Open Brain MCP | Cross-tool durable knowledge |

## Prerequisites

```bash
openagents setup
openagents honcho setup    # optional
openagents honcho mode hybrid
```

## Procedure

1. If fact must survive across tools → `open-brain` ingest
2. If session-local preference → built-in memory tools
3. Profile isolation: `openagents -p <name>` separate homes
4. Never store whistleblower/credentials in memory files

## Decision rules

| Data type | Store |
|-----------|-------|
| User preference | OpenAgents memory |
| Company policy | Open Brain (validated) |
| Audit trail | `open-rec` |
| Secrets | Env only — never memory |

## Pitfalls

- Duplicating Brain content in local memory
- Cross-profile leakage without `OPENAGENTS_HOME` discipline
- PII in markdown memory files

## Verification

- [ ] `openagents honcho status` matches config mode
- [ ] Profile `-p` uses separate `sessions.db`
- [ ] Sensitive domains use `open-whistle` boundaries
