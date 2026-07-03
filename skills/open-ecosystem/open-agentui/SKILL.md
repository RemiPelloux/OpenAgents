---
name: open-agentui
description: "Author and run OpenAgentUI YAML workflows headlessly."
version: 2.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenAgentUI, workflow, YAML, Mistral]
    category: open-ecosystem
    related_skills: [open-brain, open-code, open-dev-workflow, open-app]
---

# OpenAgentUI

Visual workflow builder — agents author **YAML**, persist, and run **without canvas**.

Default LLM: Mistral (`mistral-medium-latest`) when `MISTRAL_API_KEY` set.

## When to Use

- Multi-step automation with approvals or MCP nodes
- OpenBrain `ensure_openagentui_workflow` from mission control
- Self-authoring: agent drafts YAML → smoke test → hand to operators

Not for W4 ticket code — use `invoke_opencode` (`open-code`).

## Prerequisites

```bash
export MISTRAL_API_KEY=...
openagents auth add mistral
openagents model mistral:mistral-medium-latest
openagents dashboard   # :9119 — required for OpenBrain MCP proxy
```

Enable toolset in config:

```yaml
tools:
  enabled_toolsets:
    - openagentui
    - openos_engineering
```

OpenBrain path: `OPENAGENTS_DASHBOARD_URL=http://127.0.0.1:9119`

## Structural overview

| Path | Access |
|------|--------|
| Native tools | toolset `openagentui` |
| OpenBrain MCP | proxies dashboard REST |
| Storage | `~/.openagents/openagentui/workflows/` |

Node reference: `references/workflow-nodes.md` in this skill folder.

## Procedure — self-author (Path A)

1. `list_openagentui_workflows()` — avoid duplicates
2. Draft YAML (`start` → `agent` → `end` minimum)
3. `ensure_openagentui_workflow(name, yaml)`
4. `run_openagentui_workflow(workflow=name, inputs={...})`
5. If `waiting-approval` → `resolve_openagentui_approval` or `/OpenAgentConfig approve`

## Procedure — OpenBrain MCP (Path B)

Requires dashboard running. Tools: `list_openagentui_workflows`, `ensure_openagentui_workflow`, `run_openagentui_workflow`. Resource: `company-brain://openagentui/workflows`.

## Decision rules

| Need | Path |
|------|------|
| Local OpenAgents agent | Path A native tools |
| OpenBrain-centric agent | Path B MCP + dashboard |
| Ticket implementation | `invoke_opencode` not workflow |

## Pitfalls

- Running without `openagentui` toolset enabled
- OpenBrain MCP without dashboard (connection refused)
- Executable `arcade`/`guardrails` nodes (import-only)
- Using Grok models — OpenAgentUI expects Mistral config

## Verification

- [ ] `list_openagentui_workflows` returns saved name
- [ ] `run_openagentui_workflow` completes or pauses at approval with clear id
- [ ] Round-trip `export_openagentui_workflow_yaml` matches intent (optional)

## Operator CLI

```bash
/OpenAgentConfig list
/OpenAgentConfig run "My Automation" ticket_id=OP-42
/OpenAgentConfig approve exec_abc123
```
