---
name: open-mesh-wiring
description: "Wire mesh hops: CC-*, env, compose, MCP parity."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [mesh, wiring, integration, opencontract, docker]
    category: open-ecosystem
    related_skills: [open-contract, open-mcp-scaffold, open-rec, open-ecosystem-hub]
---

# Open Mesh Wiring

Connect OpenOS apps end-to-end: contracts, env, compose, MCP/REST parity.

## When to Use

- New producer → consumer edge (Ticket → Orch → Agents → Code → Rec)
- Local mesh test with `docker compose.mesh.yml`
- Env/SSM secrets missing between apps
- "Wire X to Y" integration tasks

Not for application business logic — wire transport + contracts only.

## Prerequisites

- `open-contract` — registry row exists before code
- OpenOS root with submodules checked out
- `docs/schema/openos-ecosystem.yaml` for port/slug map
- `docker compose` for local mesh profile

## Structural overview

| Layer | Artifact |
|-------|----------|
| Contract | `OpenContract/registry/*.yaml` |
| Producer | MCP + REST + outbox + RecEvent declare |
| Consumer | `parseInboundHop` + webhook handler |
| Env | `~/.openagents/.env`, app `.env`, SSM mesh |
| Verify | `scripts/mesh-*.sh`, app `*-e2e.sh` |

Standard hop checklist: **CC-* → envelope → correlation_id → RecEvent → Brain observation (optional)**.

## Procedure

1. **Declare** — add `CC-*` in registry (`draft`)
2. **Map** — fill relation row in app README + `openos-ecosystem.yaml`
3. **Env** — wire URLs + API keys (see `infra/docs/secrets-mesh.md`)
4. **Compose** — ensure service in `docker-compose.mesh.yml` if needed
5. **MCP install** — `openagents mcp install <app>` for agent path
6. **Implement** — producer then consumer (separate commits per repo)
7. **E2E** — cross-module script with `OPENCONTRACT_REQUIRE_SIGNATURE=1` optional
8. **Mark** — contract `verified` in registry

## Decision rules

| Hop type | Wire with |
|----------|-----------|
| Sync agent action | MCP + REST parity |
| Async audit | RecEvent + outbox worker |
| Ticket webhook | OpenContract envelope + `X-Correlation-Id` |
| Brain searchable summary | Observation ingest (non-blocking) |

| If blocked by | Then |
|---------------|------|
| Missing MCP tool | `open-mcp-scaffold` |
| Unknown CC-* | `open-brain` `domain: openos` |
| Prod secrets | SSM sync script |

## Pitfalls

- Wiring before registry declaration
- Different payload on MCP vs REST
- Blocking HTTP on RecEvent/Brain failure
- Submodule pointer not bumped at OpenOS root

## Verification

- [ ] Registry row `implemented` minimum
- [ ] E2E proves goal + same `correlation_id`
- [ ] `docker compose … up --wait` healthy for touched services
- [ ] MCP `tools/list` + REST OpenAPI both expose the action
