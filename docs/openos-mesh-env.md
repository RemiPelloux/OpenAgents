# OpenAgents ↔ OpenOS mesh environment

Canonical reference: [OpenOS/infra/docs/secrets-mesh.md](../../infra/docs/secrets-mesh.md)

Copy [infra/config/openagents.env.example](../../infra/config/openagents.env.example) into `~/.openagents/.env` when running the TikTok prospection plugin.

`OPENAGENTS_API_KEY` is **inbound auth for the gateway** (set on OpenTeam, not in OpenAgents). Outbound keys: `OPENPRO_AGENT_API_KEY`, `PROSPECTION_API_KEY`.
