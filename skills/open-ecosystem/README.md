# Open Ecosystem Skills

Agent-facing **skill.md** files for the OpenOS mesh. Loaded by OpenAgents profiles and orchestrator `required_skills`.

## Skill index (30)

| Skill | Description |
|-------|-------------|
| `open-ecosystem-hub` | Router — load first when unsure |
| `open-contract` | CC-* registry + envelopes (step 0) |
| `open-dev-workflow` | W4 end-to-end |
| `openprotocol-coder` | Developer branch/push/handoff |
| `openprotocol-integrator` | QA verify/merge |
| `open-qa` | QA sign-off: AC, tests, regression |
| `open-ticket-optimize` | Ticket grooming, AC, sizing |
| `open-mesh-wiring` | Wire mesh hops: CC-*, env, compose |
| `open-generic` | Default loop when no skill fits |
| `open-orchestrator-ops` | Industrial orchestrator gates |
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
| `open-browser` | Browser automation via Playwright MCP |
| `open-toolbox` | Autonomous tool discovery and integration |

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

## W4 profile skills (18 domain roles)

```bash
openagents openos init-profiles          # scaffold all 18
openagents openos ensure-profiles --profiles sales,security
openagents openos list-profiles
```

Orchestrator auto-provisions missing profiles before dispatch (`POST /v1/profiles/ensure`).

| Domain | Profiles |
|--------|----------|
| Orchestrator | planner, intent_classifier, skill_author |
| Engineering W4 | product_owner, developer, qa, mobile_engineer |
| Platform | mesh_engineer, contract_officer |
| Knowledge | brain_researcher, recorder_analyst |
| Commercial | sales, crm_analyst, creative, content_ops |
| Compliance | security, compliance_officer |
| Notes | notes_analyst |

| Profile | Key skills |
|---------|------------|
| `developer` | open-code, openprotocol-coder, open-mesh-wiring |
| `qa` | open-qa, openprotocol-integrator |
| `sales` | opencrm-sales-followup |
| `mesh_engineer` | open-mesh-wiring, open-contract |
| `skill_author` | can provision org profiles on orchestrator request |

## Related

- OpenOS mesh: `OpenOS/docs/schema/openos-ecosystem.yaml`
- Brain corpus: `OpenOS/docs/BRAIN-CORPUS.yaml`
