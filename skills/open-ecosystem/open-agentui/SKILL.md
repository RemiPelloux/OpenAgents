---
name: open-agentui
description: "Author, install, and run OpenAgentUI workflows headlessly (YAML/MCP/tools) — Mistral default LLM."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenAgentUI, Workflow, YAML, Mistral, OpenBrain, MCP, SelfAuthoring]
    related_skills: [open-dev-workflow, open-code, open-brain, open-ecosystem-hub]
---

# OpenAgentUI — Complete Headless Workflow Guide

**OpenAgentUI** is OpenAgents' local visual workflow builder. You do **not** need the canvas to create or run workflows — agents can author YAML, persist workflows, and execute them via **native tools** or **OpenBrain MCP**.

**Default LLM:** Mistral (`mistral-medium-latest`) when `MISTRAL_API_KEY` is set. OpenAgentUI does **not** use xAI Grok / grok-build.

---

## Quick answers

| Question | Answer |
|----------|--------|
| Is there an MCP to build workflows? | **Yes — two paths.** (1) OpenAgents native tools in toolset `openagentui`. (2) OpenBrain Knowledge MCP proxies the OpenAgents dashboard REST API. |
| Can OpenAgents create its own workflows? | **Yes.** Enable toolset `openagentui`, then call `ensure_openagentui_workflow` or `create_openagentui_workflow_from_yaml`. An agent can design YAML, validate it, save it, and run it in the same session. |
| Is the UI required? | **No.** UI (`/OpenAgentUI true`) is optional for editing. Execution always goes through the Python engine. |

---

## Architecture (who talks to whom)

```
┌─────────────────┐     toolset openagentui      ┌──────────────────────┐
│  OpenAgents     │ ───────────────────────────► │ ~/.openagents/       │
│  (any agent)    │   ensure/create/run YAML     │ openagentui/workflows│
└────────┬────────┘                              └──────────▲───────────┘
         │ MCP: openbrain-knowledge                          │
         ▼                                                   │
┌─────────────────┐   HTTP :9119/api/openagentui/*          │
│  OpenBrain      │ ─────────────────────────────────────────┘
│  Knowledge MCP  │   (requires `openagents dashboard`)
└─────────────────┘
```

---

## Prerequisites

### 1. Environment (never commit secrets)

```bash
export MISTRAL_API_KEY=...
export LLM_MODEL=mistral-medium-latest
export LLM_BASE_URL=https://api.mistral.ai/v1
```

OpenBrain (optional MCP path):

```bash
export OPENAGENTS_DASHBOARD_URL=http://127.0.0.1:9119
```

### 2. Persist Mistral in OpenAgents

```bash
openagents auth add mistral
openagents model mistral:mistral-medium-latest
```

### 3. Start the dashboard (required for OpenBrain MCP proxy)

```bash
openagents dashboard
# REST API: http://127.0.0.1:9119/api/openagentui/*
```

### 4. Enable the `openagentui` toolset on your agent

In `~/.openagents/config.yaml` (or profile):

```yaml
tools:
  enabled_toolsets:
    - openagentui
    - openos_engineering   # invoke_opencode / invoke_codex
```

Or enable per-session via the tools picker in CLI/gateway. Without this, the agent **cannot** call workflow authoring tools.

### 5. OpenBrain MCP (optional — for agents using OpenBrain as MCP hub)

```yaml
mcp_servers:
  openbrain-knowledge:
    url: ${OPENBRAIN_API_URL}/api/v1/mcp/knowledge
    headers:
      Authorization: Bearer ${AXON_AGENT_API_KEY}
```

Reload: `/mcp reload`

---

## Path A — OpenAgents creates workflows itself (recommended)

An OpenAgents agent with toolset `openagentui` enabled can **self-author** end-to-end:

### Step 1 — Discover what exists

```
list_openagentui_workflows()
```

### Step 2 — Author YAML (agent writes this)

Minimum valid workflow:

```yaml
id: wf_my_automation
name: My Automation
description: Created by agent — no UI
nodes:
  - id: start
    type: start
    data:
      inputVariables:
        - name: ticket_id
          required: true
  - id: summarize
    type: agent
    data:
      label: Summarize ticket context
      instructions: "Summarize ticket {{ ticket_id }} for a dev handoff."
      model: mistral-medium-latest
      tools: []
      maxIterations: 5
  - id: end
    type: end
    data:
      outputMapping:
        brief: "{{ nodes.summarize.output }}"
edges:
  - id: e1
    source: start
    target: summarize
  - id: e2
    source: summarize
    target: end
```

Copy-paste starter: `openagentui/templates/example_linear_agent.yaml`

### Step 3 — Install idempotently (create only if missing)

```
ensure_openagentui_workflow(
  name="My Automation",
  yaml="<full yaml string>"
)
```

Returns `{ created: true }` on first install, `{ created: false, workflow: {...} }` if name already exists.

### Step 4 — Run headlessly

```
run_openagentui_workflow(
  workflow="My Automation",
  inputs={ "ticket_id": "OP-42" }
)
```

### Step 5 — Human approval nodes (if any)

If status is `waiting-approval`:

```
resolve_openagentui_approval(execution_id="...", decision="approved")
```

Or user runs: `/OpenAgentConfig approve <execution_id>`

### Self-authoring recipe (agent playbook)

When the user asks *"build a workflow that …"*:

1. `list_openagentui_workflows()` — avoid duplicates
2. Draft YAML with correct node types (see reference below)
3. `ensure_openagentui_workflow(name, yaml)` — persist
4. `export_openagentui_workflow_yaml(workflow=name)` — verify round-trip (optional)
5. `run_openagentui_workflow(workflow=name, inputs={...})` — smoke test
6. Report workflow id, name, and how to re-run via `/OpenAgentConfig run`

---

## Path B — OpenBrain MCP (remote agents / OpenBrain-centric)

OpenBrain Knowledge MCP exposes workflow tools that **proxy** the OpenAgents dashboard:

| MCP tool | Purpose |
|----------|---------|
| `list_openagentui_workflows` | Full list of saved workflows |
| `ensure_openagentui_workflow` | Create from YAML if name missing |
| `create_openagentui_workflow` | Always create/overwrite from YAML |
| `run_openagentui_workflow` | Execute by name or id |

| MCP resource | Purpose |
|--------------|---------|
| `company-brain://openagentui/workflows` | JSON snapshot of all workflows |

**Requirement:** `openagents dashboard` must be running and reachable at `OPENAGENTS_DASHBOARD_URL`.

Example MCP call:

```json
{
  "tool": "ensure_openagentui_workflow",
  "arguments": {
    "name": "Nightly digest",
    "yaml": "id: wf_digest\nname: Nightly digest\nnodes: [...]\nedges: [...]"
  }
}
```

OpenBrain agents can therefore orchestrate OpenAgents workflows without local `openagentui` toolset — as long as the dashboard is up.

---

## Native agent tools (toolset `openagentui`)

| Tool | When to use |
|------|-------------|
| `list_openagentui_workflows` | Inventory before creating |
| `ensure_openagentui_workflow` | Idempotent install by human-readable name |
| `create_openagentui_workflow_from_yaml` | Force create/overwrite by YAML `id` |
| `export_openagentui_workflow_yaml` | Export for git review or editing |
| `run_openagentui_workflow` | Execute saved workflow |
| `resolve_openagentui_approval` | Resume paused approval gate |

Coding delegation (toolset `openos_engineering`):

| Tool | When |
|------|------|
| `invoke_opencode` | W4 ticket implementation (OpenOS OpenCode fork) |
| `invoke_codex` | OpenAI Codex CLI (`codex exec`) fallback |

---

## Node type reference (v1)

| Type | Purpose | Key `data` fields |
|------|---------|-------------------|
| `start` | Seed variables | `inputVariables[]` with `name`, `required`, `defaultValue` |
| `agent` | One LLM turn | `instructions`, `model`, `tools[]` (toolset names), `maxIterations` |
| `mcp` | Deterministic tool call | `mcpTool`, `mcpParams`, `outputField` |
| `transform` | Python sandbox | `code` |
| `http` | Outbound HTTP | `url`, `method`, `headers`, `body` |
| `if-else` | Branch | `condition` — edges use handles `true` / `false` |
| `while` | Loop | `condition`, `maxIterations` — handles `loop` / `exit` |
| `set-state` | Set variable | `stateKey`, `stateValue` (supports `{{ }}`) |
| `user-approval` | Pause for human | — resume via approve/reject |
| `end` | Finish | `outputMapping` or all variables |
| `note` | Canvas annotation | no-op at runtime |
| `arcade`, `guardrails` | Import only | **Not executable** in OpenAgentUI |

### Templating

- `{{ variable_name }}` — workflow variable
- `{{ nodes.<node_id>.output }}` — prior node output
- `{{ nodes.<node_id>.output.field }}` — nested field

### Conditions (`if-else` / `while`)

Python-like expressions via safe AST evaluator — no function calls:

```python
duplicate_check.duplicate == True
score > 80 and region == "EU"
```

---

## CLI (operator, no agent)

```bash
openagents dashboard                    # REST API
/OpenAgentUI true                       # optional visual editor
/OpenAgentConfig list                   # list workflows
/OpenAgentConfig show "My Automation"   # inspect graph
/OpenAgentConfig run "My Automation" ticket_id=OP-42
/OpenAgentConfig approve exec_abc123
```

---

## REST API (scripts / OpenBrain client)

| Method | Path | Body |
|--------|------|------|
| GET | `/api/openagentui/workflows` | — |
| POST | `/api/openagentui/workflows/from-yaml` | `{ "yaml": "..." }` |
| PUT | `/api/openagentui/workflows/{id}/from-yaml` | `{ "yaml": "..." }` |
| GET | `/api/openagentui/workflows/{id}/yaml` | — |
| POST | `/api/openagentui/workflows/{id}/run` | `{ "inputs": {} }` |
| GET | `/api/openagentui/catalog` | toolsets/tools for node pickers |

---

## Bundled templates

| Template | Install |
|----------|---------|
| TikTok prospection (flagship) | `POST /api/openagentui/templates/tpl_openpro_tiktok_prospection/install` |
| Linear agent example (YAML) | Copy `openagentui/templates/example_linear_agent.yaml` → `ensure_openagentui_workflow` |

---

## Validation checklist (before marking workflow "done")

- [ ] YAML has unique `id` and human `name`
- [ ] Graph has exactly one `start` and at least one `end`
- [ ] Every edge `source`/`target` references existing node ids
- [ ] `if-else` / `while` edges use correct handles (`true`/`false`, `loop`/`exit`)
- [ ] `agent` nodes use `mistral-medium-latest` or blank (falls back to env)
- [ ] `mcp` nodes use real tool names from `GET /api/openagentui/catalog`
- [ ] `ensure_openagentui_workflow` returns `created: true` on first run
- [ ] `run_openagentui_workflow` completes or pauses at expected approval gate
- [ ] OpenBrain MCP: `list_openagentui_workflows` returns the new workflow (if using Path B)

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tool not found | Enable toolset `openagentui` in config |
| OpenBrain MCP fails | Start `openagents dashboard`; check `OPENAGENTS_DASHBOARD_URL` |
| Agent node fails | Set `MISTRAL_API_KEY` + `openagents auth add mistral` |
| YAML parse error | Ensure top-level mapping; required fields `id`, `name`, `nodes`, `edges` |
| Workflow name collision | Use `ensure_*` (skip) or new `id` with `create_*` (overwrite) |

---

## Related docs

- [docs/openagentui.md](../../docs/openagentui.md) — engine + REST contract
- [skills/open-ecosystem/open-brain/SKILL.md](../open-brain/SKILL.md) — OpenBrain MCP setup
- [skills/open-ecosystem/open-dev-workflow/SKILL.md](../open-dev-workflow/SKILL.md) — W4 ticket → OpenCode flow
