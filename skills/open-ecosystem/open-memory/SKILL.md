---
name: open-memory
description: "Use when configuring OpenAgents memory — built-in store, Honcho, plugins, session recall, and Open Brain bridges."
version: 1.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [memory, openagents, honcho, mem0, session, recall, persistence]
    related_skills: [open-ecosystem-hub, openagents, open-brain]
---

# Open Memory

**Open Memory** is how OpenAgents remembers users across sessions: built-in memory files, optional Honcho/Mem0 plugins, session search, and bridges to **Open Brain** for cross-tool knowledge.

## When to use

- Enabling or tuning agent memory in OpenAgents
- Honcho setup, memory mode (`hybrid` / `honcho` / `local`)
- Debugging "agent forgot X" or duplicate memory writes
- Deciding what stays in OpenAgents vs Open Brain

## Memory locations

| Store | Path / mechanism | Scope |
|-------|------------------|--------|
| Config + secrets | `~/.openagents/` (or profile dir) | Per profile |
| Built-in memory | `memory/` under OPENAGENTS_HOME | Agent-managed markdown/DB |
| Sessions | `sessions.db` (SQLite + FTS) | Conversation history |
| Honcho | External Honcho API | Dialectic / peer memory |
| Open Brain | External MCP (see `open-brain`) | Cross-tool shared brain |

Profiles isolate memory: `openagents -p <name>` uses `~/.openagents/profiles/<name>/`.

## Quick setup

```bash
openagents setup                    # initial config
openagents honcho setup             # optional Honcho integration
openagents honcho mode hybrid         # hybrid | honcho | local
openagents honcho status
```

Config keys live in `~/.openagents/config.yaml` under `memory:` and related sections.

## Built-in memory tools

The agent can call memory tools (when memory toolset enabled):

- Store user preferences, environment facts, lessons learned
- Recall relevant memories into context (respecting prompt caching — loaded at session start)

Check availability: `openagents doctor` and `openagents tools list`.

## Honcho integration

```bash
openagents honcho map <session-name>    # map cwd → Honcho session
openagents honcho peer --user NAME --ai NAME
openagents honcho identity SOUL.md      # seed AI peer identity
openagents honcho migrate               # OpenClaw → OpenAgents + Honcho guide
```

Modes:

- **local** — OpenAgents built-in only
- **honcho** — Honcho primary
- **hybrid** — both (needs clear write rules to avoid duplicates)

## Open Brain bridge

Use when memory must survive **outside** OpenAgents (Cursor, Claude Desktop, mobile):

1. Deploy Open Brain (`open-brain` skill)
2. Register MCP server in `config.yaml`
3. Document which fact classes go to Honcho vs Open Brain vs built-in

**Rule of thumb:**

| Data type | Prefer |
|-----------|--------|
| Session workflow, tool lessons | Built-in / Honcho |
| User identity & long-lived prefs | Open Brain |
| Compliance / whistleblower | Isolated DB only (`open-whistle`) |
| Open Pro user profile | Backend API — not agent memory |

## Prompt caching note

OpenAgents loads memory into the **system prompt at session start**. Do not expect mid-conversation memory reloads without a new session or explicit compression path — this protects API prompt cache costs.

## Common pitfalls

1. **Wrong profile** — memory written under default profile, read from `-p coder`
2. **HERMES_HOME vs OPENAGENTS_HOME** — legacy env vars; use `OPENAGENTS_HOME` explicitly in services
3. **Storing secrets in memory** — memory files may sync or log; use credential store
4. **Duplicate writes** — hybrid mode without domain split fills both stores with conflicting text

## Verification checklist

- [ ] `openagents doctor` reports memory backend healthy
- [ ] `echo $OPENAGENTS_HOME` matches expected profile
- [ ] Test recall: store a unique fact, new session, ask agent to recall
- [ ] If Honcho: `openagents honcho status` connected
- [ ] If Open Brain: MCP tools visible after `/mcp reload`
