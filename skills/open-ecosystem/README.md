# Open Ecosystem Skills

Agent-facing **skill.md** files for the OpenOS mesh. Loaded by OpenAgents profiles and orchestrator `required_skills`.

## Skill index (25)

| Skill | Description |
|-------|-------------|
| `open-ecosystem-hub` | Router — load first when unsure |
| `open-contract` | CC-* registry + envelopes (step 0) |
| `open-dev-workflow` | W4 end-to-end |
| `openprotocol-coder` | Developer branch/push/handoff |
| `openprotocol-integrator` | QA verify/merge |
| `open-code` | `invoke_opencode` |
| `open-ticket` | OpenTicket MCP |
| `open-rec` | RecEvent audit |
| `open-brain` | Doc RAG + graph |
| `open-brain-orchestrator` | Missions + ask_brain |
| `open-orchestrator-intent` | NL → NormalizedGoal |
| `open-orchestrator-plan` | Goals → dispatch |
| `open-mcp-scaffold` | Add MCP + REST tools |
| `open-notes` | Meeting intelligence |
| `open-team` | Harvest + dispatch |
| `open-sec` | Security findings → tickets |
| `open-center` | Human GUI Phase 2 |
| `opencrm-sales-followup` | CRM staged updates |
| `openpro-tiktok-prospection` | TikTok vertical |
| `open-creative` | Image deliverables |
| `open-agentui` | YAML workflows |
| `open-memory` | Agent session memory |
| `open-app` | CLI/TUI/desktop |
| `open-whistle` | Compliance intake |
| `open-pro` | Flutter mobile |

## Authoring standard

Every skill includes:

1. **When to Use** — scope boundaries
2. **Prerequisites** — env, services, profiles
3. **Structural overview** — objects and ports
4. **Procedure** — ordered workflows
5. **Decision rules** — if/then tables
6. **Pitfalls** — negative constraints
7. **Verification** — checklist

`description` frontmatter ≤ 60 characters.

## W4 profile skills

```bash
openagents openos init-profiles
```

| Profile | Skills |
|---------|--------|
| `developer` | open-code, open-ticket, open-dev-workflow, openprotocol-coder |
| `qa` | open-code, open-ticket, open-dev-workflow, openprotocol-integrator |
| `product_owner` | open-ticket, open-dev-workflow |
| `planner` | open-orchestrator-plan, open-ecosystem-hub |

## Related

- OpenOS mesh: `OpenOS/docs/schema/openos-ecosystem.yaml`
- Brain corpus: `OpenOS/docs/BRAIN-CORPUS.yaml`
