# OpenAgentUI — Visual Workflow Builder

**OpenAgentUI** is OpenAgents' local-only visual workflow builder: a rebranded,
natively-integrated fork of [firecrawl/open-agent-builder](https://github.com/firecrawl/open-agent-builder).
The upstream marketing site, Convex (cloud DB), Clerk (cloud auth), Arcade.dev,
E2B, and CopilotKit dependencies are stripped out entirely. What remains — the
React Flow canvas and node-graph schema — is executed by a new Python engine
(`openagentui/`) that dispatches every node directly into OpenAgents' **own**
agent loop, tool registry, plugins, and MCP catalog. No separate LLM keys or
cloud accounts are required.

Brand: **OpenAgentUI**, part of the **OpenPro** product family.

---

## Architecture

```
apps/openagentui/            Next.js + React Flow canvas (frontend, rebranded)
openagentui/                 Python execution engine
├── schema.py                 WorkflowNode/Edge/Execution dataclasses
├── store.py                  JSON-file persistence (~/.openagents/openagentui/)
├── templating.py              {{ variable }} / {{ nodes.<id>.output }} substitution
├── safe_eval.py                Constrained AST evaluator for if-else/while conditions
├── tool_catalog.py            Lists real OpenAgents toolsets/tools/MCP servers
├── engine.py                  Graph walker: traversal, branching, pause/resume
├── approvals.py                Resolve a paused user-approval node and resume
├── nodes/                      One executor module per node type
└── templates/                  Bundled scenario JSON (see below)
openagents_cli/
├── openagentui_server.py       FastAPI routes, mounted on the dashboard server
├── openagentui_cmd.py           /OpenAgentUI shared command
└── openagentui_config_cmd.py    /OpenAgentConfig shared command
tools/openagentui_tool.py       Agent-callable tools (list/run/approve workflows)
```

The frontend talks to `openagents_cli/openagentui_server.py` (mounted into the
existing local dashboard, default port `9119`) via `/api/openagentui/*`, which
is rewritten by `apps/openagentui/next.config.js`. Every route inherits the
dashboard's existing loopback-token auth — no separate accounts.

---

## Running it

```bash
# One-time: install the frontend's dependencies
cd apps/openagentui && npm install && cd ../..

# Start the dashboard (serves REST API — auto-started by /OpenAgentUI true)
openagents dashboard --no-open

# In OpenAgents CLI — bring builder online (no browser popup)
/OpenAgentUI true

# Optional: also open the browser
/OpenAgentUI true open

# Or check status / stop
/OpenAgentUI status
/OpenAgentUI stop
```

`/OpenAgentUI true|start` brings the builder **online** in the background (and
auto-starts the dashboard API if needed). It does **not** open a browser unless
you pass `open`: `/OpenAgentUI true open`. `/OpenAgentUI stop` tears it down;
`/OpenAgentUI status` reports whether it's running and its URL. State (PID, port) is
tracked in `~/.openagents/openagentui/server.json`.

Workflows/executions/approvals are plain JSON files under
`~/.openagents/openagentui/{workflows,executions,approvals,mcp_servers}/`.

---

## Running saved workflows headlessly

The visual builder UI does **not** need to be running to execute a saved
workflow — `/OpenAgentConfig` drives the engine directly:

```text
/OpenAgentConfig                                  # list saved workflows
/OpenAgentConfig show <name-or-id>                # inspect nodes/edges
/OpenAgentConfig run <name-or-id> key=value ...    # run it (streams node progress)
/OpenAgentConfig <name-or-id> key=value ...        # shorthand for `run`
/OpenAgentConfig approve <execution_id>            # resolve a paused approval
/OpenAgentConfig reject <execution_id>             # reject it
```

Terminal equivalents: `openagents openagentui start|stop|status` and
`openagents openagent-config list|show|run|approve|reject`.

### Other agents can trigger workflows too

Toolset `openagentui` (opt-in, like `openpro_prospection`) exposes agent-callable
tools so any agent/subagent can list, author, run, and approve workflows without
the canvas:

- `list_openagentui_workflows()`
- `ensure_openagentui_workflow(name, yaml)` — create only when missing
- `create_openagentui_workflow_from_yaml(yaml)`
- `export_openagentui_workflow_yaml(workflow)`
- `run_openagentui_workflow(workflow, inputs)`
- `resolve_openagentui_approval(execution_id, decision)`

Default LLM for `agent` nodes: **Mistral** when `MISTRAL_API_KEY` / `LLM_MODEL`
are set (`openagentui/llm_defaults.py`). OpenAgentUI does **not** use xAI Grok /
grok-build.

Coding delegation (toolset `openos_engineering`): `invoke_opencode` (W4 tickets),
`invoke_codex` (OpenAI Codex CLI).

Skill: `skills/open-ecosystem/open-agentui/SKILL.md` — headless YAML authoring.

### OpenBrain integration

OpenBrain Knowledge MCP proxies the local OpenAgents dashboard when
`OPENAGENTS_DASHBOARD_URL` is set (default `http://127.0.0.1:9119`):

- Resource `company-brain://openagentui/workflows` — full workflow list
- MCP tools: `list_openagentui_workflows`, `ensure_openagentui_workflow`,
  `create_openagentui_workflow`, `run_openagentui_workflow`

### REST (for other OpenOS apps)

| Method & path | Purpose |
|---|---|
| `GET /api/openagentui/workflows` | List saved workflows |
| `POST /api/openagentui/workflows` | Create a workflow |
| `POST /api/openagentui/workflows/from-yaml` | Create from YAML `{ "yaml": "..." }` |
| `GET /api/openagentui/workflows/{id}/yaml` | Export workflow as YAML |
| `PUT /api/openagentui/workflows/{id}/from-yaml` | Upsert from YAML |
| `GET/PUT/DELETE /api/openagentui/workflows/{id}` | Read/update/delete |
| `GET /api/openagentui/templates` | List bundled templates |
| `POST /api/openagentui/templates/{id}/install` | Copy a template into a new workflow |
| `GET /api/openagentui/catalog` | Toolsets/tools/MCP servers for node pickers |
| `POST /api/openagentui/workflows/{id}/run` | Run to completion/pause, return the execution |
| `POST /api/openagentui/workflows/{id}/execute-stream` | Same, as a Server-Sent Events stream |
| `GET /api/openagentui/workflows/{id}/executions` | List past executions |
| `GET /api/openagentui/executions/{id}` | Fetch one execution's state |
| `POST /api/openagentui/executions/{id}/approve\|reject` | Resolve a paused `user-approval` node |

Registered in OpenContract as `CC-OA-OAUI-001` (trigger) and `CC-OA-OAUI-002`
(status/approval) — see `OpenContract/registry/openagentui.yaml`.

---

## Node execution mapping (v1)

| Node type | v1 behavior |
|---|---|
| `start` | Seeds workflow variables from `inputVariables` defaults + caller-supplied inputs |
| `agent` | One LLM turn via `run_agent.AIAgent.run_conversation()` — `tools` selects real OpenAgents toolset names |
| `mcp` | Deterministic call into any tool registered in `tools/registry.py` (native, plugin, or MCP-catalog) — no LLM reasoning |
| `transform` | Runs a Python snippet via `tools/code_execution_tool.py`'s sandbox, with workflow variables injected as `INPUT` |
| `http` | Direct outbound `httpx` request |
| `if-else` | Evaluates `condition` with the constrained evaluator in `safe_eval.py` (never a bare `eval()`); branches on `"true"`/`"false"` outgoing-edge handles |
| `while` | Same evaluator, loops until false or `maxIterations`; branches `"loop"`/`"exit"` |
| `set-state` | Sets one variable (`stateKey` / `stateValue`, template-substituted) |
| `user-approval` | Pauses the run (`waiting-approval`), persists a `PendingApproval` record; resumed by `/OpenAgentConfig approve\|reject` or the REST/MCP approval-resolve calls |
| `note` | UI-only annotation, no-op |
| `end` | Finalizes output — `outputMapping` (template-substituted) or all variables |
| `arcade`, `guardrails` | Kept in the schema for import compatibility with upstream templates, but fail with a clear "not supported in OpenAgentUI" error — both depend on paid external SaaS not integrated in this fork |

### `{{ }}` templating

Node config fields support two placeholder forms:

- `{{ someVariable }}` — looks up `execution.variables['someVariable']` (supports dotted paths into nested dicts, e.g. `{{ user.profile.email }}`)
- `{{ nodes.<node_id>.output }}` / `{{ nodes.<node_id>.output.field }}` — looks up a previous node's result

### Conditions (`if-else` / `while`)

Conditions are Python-like boolean expressions evaluated by a whitelisted AST
walker (`openagentui/safe_eval.py`) — comparisons, `and`/`or`/`not`, `in`/`not
in`, arithmetic, list/tuple literals, and dotted/subscript access into
dict-shaped variables (e.g. `duplicate_check.duplicate` or `items[0]`). No
function calls, imports, or comprehensions are allowed — this is never a bare
`eval()` against workflow-authored text.

---

## Authoring a new scenario

1. `/OpenAgentUI true` to open the canvas, **or** author YAML and POST to
   `/api/openagentui/workflows/from-yaml`, **or** call
   `ensure_openagentui_workflow` / `create_openagentui_workflow_from_yaml` from
   an agent with toolset `openagentui` enabled.
2. Use the `agent` node for LLM reasoning steps and `mcp` nodes for
   deterministic calls into existing tools/plugins — check
   `GET /api/openagentui/catalog` (or the builder's node config pickers) for
   the exact tool names available in your install.
3. Save, then either run it from the canvas, `/OpenAgentConfig run <name>`,
   or have another agent call `run_openagentui_workflow`.

### Flagship bundled scenario

`openagentui/templates/openpro_tiktok_prospection.json` ports the existing
`plugins/openpro_prospection` flow into a visual workflow: TikTok lead
discovery → duplicate check → OpenPro company + job post provisioning →
outreach (email/DM) → status report. Install it via `/OpenAgentUI true` (the
builder's template gallery) or `POST /api/openagentui/templates/tpl_openpro_tiktok_prospection/install`.

---

## Explicit scope boundaries

- `arcade` and `guardrails` nodes render/import/export correctly but are not
  executable — no Arcade.dev or moderation-API integration is added.
- No Playwright/browser E2E suite is bundled; frontend correctness is
  verified via `next build` plus the pytest coverage under
  `tests/openagentui/` and `tests/openagents_cli/test_openagentui_*.py`.
- The OpenContract lifecycle for `CC-OA-OAUI-*` is `implemented` (registered,
  wired, smoke-testable) — full `verified` E2E hardening across
  OpenTeam/OpenOrchestrator is a natural fast-follow once a consuming app
  calls the endpoint.

See also: [docs/PELLOUX_GUIDELINES.md](PELLOUX_GUIDELINES.md),
[apps/openagentui/README.md](../apps/openagentui/README.md) (frontend
attribution notice).
