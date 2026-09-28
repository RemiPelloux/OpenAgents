# OpenAgents ↔ OpenOS mesh environment

Canonical reference: [OpenOS/infra/docs/secrets-mesh.md](../../infra/docs/secrets-mesh.md)

Copy [infra/config/openagents.env.example](../../infra/config/openagents.env.example) into `~/.openagents/.env` when running the TikTok prospection plugin.

`OPENAGENTS_API_KEY` is **inbound auth for the gateway** (set on OpenTeam, not in OpenAgents). Outbound keys: `OPENPRO_AGENT_API_KEY`, `PROSPECTION_API_KEY`.

## OpenProtocol git (coder → integrator)

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | Machine PAT for `openprotocol-coder` push + `openprotocol-integrator` merge |

Skills: `openprotocol-coder` (developer), `openprotocol-integrator` (qa). On AWS, store token in SSM and sync to `~/.openagents/.env` — machine PAT only, no interactive CLI login.

## OpenBrain (Axon) — doc RAG + knowledge MCP

| Variable | Purpose |
|----------|---------|
| `OPENBRAIN_API_URL` | OpenBrain API base (default `http://localhost:3001`) |
| `OPENBRAIN_URL` | Alias for observation ingest base URL |
| `AXON_AGENT_API_KEY` | Agent key for MCP Knowledge + bulk doc sync + observations |
| `OPENBRAIN_API_KEY` | Alias for observation ingest Bearer auth |
| `OPENBRAIN_AGENT_API_KEY` | Alias accepted by OpenOS `brain-sync-docs.sh` |

**Observations (CC-BRAIN-001):** `invoke_opencode` emits `agent.run.started|completed|failed` to `POST /api/v1/brain/observations` when `OPENBRAIN_URL` + API key are set. Non-blocking.

**MCP Knowledge endpoint:** `$OPENBRAIN_API_URL/api/v1/mcp/knowledge`

**OpenOS doc search:** MCP tool `search_knowledge` with `domain: "openos"` and optional `app: "openticket"`.

**Sync OpenOS docs into Axon** (from OpenOS repo root, OpenBrain running):

```bash
./scripts/validate-brain-corpus.sh
./scripts/brain-sync-docs.sh --tier 0
```

**Cadence:** manual after doc changes; optional nightly CI dry-run. See [OpenBrain/docs/OPENOS-DOCS-RAG.md](../../OpenBrain/docs/OPENOS-DOCS-RAG.md).

## OpenCRM — commercial source of truth (W1)

| Variable | Purpose |
|----------|---------|
| `OPENCRM_API_URL` | OpenCRM REST API base (default `http://localhost:3010`) |

Plugin: `opencrm_sales` (`search_accounts`, `check_account_duplicate`, `get_account`,
`propose_crm_update`). MCP (stdio `optional-mcps/opencrm`): `enrich_contact`,
`list_decision_makers`, `list_contacts_needing_enrichment`, plus full CRUD.
Also consumed by `openpro_prospection`'s `check_company_duplicate` and
`upsert_crm_from_lead` (CC-W1-004, CC-W1-006). Skills: **`opencrm-sales-followup`**,
**`opencrm-contact-enrichment`**.
