# OpenAgentUI

OpenPro's local visual workflow builder for [OpenAgents](../../README.md) — a
React Flow canvas for chaining `start / agent / mcp / transform / if-else /
while / user-approval / set-state / http / note / end` nodes into an
executable graph, saved locally and run by OpenAgents' own agent loop and
tool registry (see `../../openagentui/`).

No cloud account, no external database: this app talks only to the FastAPI
routes OpenAgents' existing dashboard server exposes at
`/api/openagentui/*` (mounted from `openagents_cli/openagentui_server.py`),
which persist workflows as JSON files under `~/.openagents/openagentui/`.

## Running

```bash
npm install
npm run build && npm run start   # production, or `npm run dev` while iterating
```

Then, separately, make sure the OpenAgents dashboard is running so the API
calls above resolve:

```bash
openagents dashboard
```

Or drive both from the OpenAgents CLI: `/OpenAgentUI true`.

## Attribution

The workflow canvas concept (node types, JSON graph schema, and the overall
"visual builder over an agent runtime" idea) originates from
[firecrawl/open-agent-builder](https://github.com/firecrawl/open-agent-builder)
(MIT License, Copyright (c) Firecrawl). This app is an independent,
from-scratch implementation targeting OpenAgents' own execution engine and
local-file storage instead of the original's Convex database, Clerk auth,
Arcade.dev tool catalog, and E2B sandbox — none of that upstream code is
vendored here — but the product shape and node-type vocabulary are
deliberately kept compatible so workflows are easy to reason about across
both projects. Thank you to the Firecrawl team for the original concept.

## Structure

```
app/                         Next.js App Router pages (list, editor+run)
components/workflow-builder/ React Flow canvas, node palette, config panel, run log
lib/workflow/types.ts        Graph schema — mirrors OpenAgents/openagentui/schema.py
lib/api.ts                   Fetch client for /api/openagentui/*
```
