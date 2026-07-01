# OpenAgents ↔ OpenOS mesh environment

Canonical reference: [OpenOS/infra/SECRETS-MESH.md](https://github.com/RemiPelloux/OpenOS/blob/main/infra/SECRETS-MESH.md)

Copy [infra/ecosystem/config/openagents.mesh.env.example](https://github.com/RemiPelloux/OpenOS/blob/main/infra/ecosystem/config/openagents.mesh.env.example) into `~/.openagents/.env` when running the TikTok prospection plugin.

`OPENAGENTS_API_KEY` is **inbound auth for the gateway** (set on OpenTeam, not in OpenAgents). Outbound keys: `OPENPRO_AGENT_API_KEY`, `PROSPECTION_API_KEY`.
