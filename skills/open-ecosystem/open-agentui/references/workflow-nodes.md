# OpenAgentUI — node types and templating

## Node types (v1)

| Type | Purpose | Key `data` fields |
|------|---------|-------------------|
| `start` | Seed variables | `inputVariables[]` |
| `agent` | LLM turn | `instructions`, `model`, `tools[]`, `maxIterations` |
| `mcp` | Tool call | `mcpTool`, `mcpParams`, `outputField` |
| `transform` | Python sandbox | `code` |
| `http` | Outbound HTTP | `url`, `method`, `headers`, `body` |
| `if-else` | Branch | `condition` — edges `true` / `false` |
| `while` | Loop | `condition`, `maxIterations` |
| `set-state` | Set variable | `stateKey`, `stateValue` |
| `user-approval` | Human gate | resume via approve/reject |
| `end` | Finish | `outputMapping` |
| `note` | Canvas only | no-op at runtime |

Not executable: `arcade`, `guardrails` (import only).

## Templating

- `{{ variable_name }}`
- `{{ nodes.<node_id>.output }}`
- `{{ nodes.<node_id>.output.field }}`

## Conditions

Python-like AST — no function calls:

```python
duplicate_check.duplicate == True
score > 80 and region == "EU"
```

## Starter YAML

See `openagentui/templates/example_linear_agent.yaml` in OpenAgents repo.
