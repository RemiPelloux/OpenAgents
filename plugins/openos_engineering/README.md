# OpenOS engineering plugin (W4)

W4 integration: **OpenAgents Dev/QA → OpenCode headless → OpenTicket webhook → OpenRec**.

## Tool

- `invoke_opencode(ticket_id, mode=implement|review|test)` — OpenCode loads full ticket via `OPENTICKET_TICKET_ID`; plugin sends a minimal task prompt only.

## CLI

```bash
openagents openos init-profiles
openagents openos handle-run --payload '{"agent_profile":"developer","task_context":{"ticket_id":"..."}}'
```

## Environment

| Variable | Purpose |
|----------|---------|
| `OPENOS_OPENCODE_PATH` | Compiled OpenCode binary (preferred) |
| `OPENTICKET_API_URL` | OpenTicket REST (default `http://localhost:3020`) |
| `OPENTICKET_API_TOKEN` | Optional Bearer auth |
| `OPENTICKET_CORRELATION_ID` | Set automatically from ticket; propagated to OpenCode |
| `OPENREC_URL` | RecEvent ingest after invoke |
| `OPENOS_ROOT` | Fallback path to OpenCode when binary not installed |

## W4 flow

1. PO creates ticket (MCP) → `correlation_id` on ticket
2. Dev `invoke_opencode` → `todo→in_progress`
3. OpenCode session-complete webhook → ticket `in_review` (not plugin)
4. Orchestrator assigns QA → `openagents openos handle-run`
5. OpenRec receives `agent.run.completed` and `code.implementation.completed`

## Kanban lane

`kanban_lane.spawn_opencode_lane` is available for boards that assign tasks to `opencode`; wire via custom `spawn_fn` at dispatch time.
