# OpenAgents — TODO

**Maturity:** L1 → L2  
**Registry:** OpenOS [ECOSYSTEM-MATURITY](../docs/ECOSYSTEM-MATURITY.md) · step 2

## Done

- [x] MCP hub + multi-provider runtime
- [x] PO / Dev / QA profiles + `invoke_opencode`
- [x] Signed RecEvent outbox + mesh contract wrap
- [x] CC-W4-001 `create_ticket` + subtask delegation
- [x] OpenAgentUI + prospection → OpenCRM plugin
- [x] `open-brain-orchestrator` skill

## Missing (L2 exit)

- [x] **`agent.run.started|completed|failed`** Brain observations after each profile run (CC-BRAIN-001)
- [ ] Dedicated **mesh E2E script** (Agents → Ticket → Code → Rec) or extend W4 assert path
- [ ] **OpenOrchestrator approval hook** — blocked until Orch `POST /v1/runs` live
- [ ] Replace **file interim outbox** with Postgres outbox (L4 mesh gate)
- [ ] ETA mesh tools parity with OpenTicket MCP

## L4

- [ ] CI mesh matrix row in OpenOS `mesh-gate.yml`
- [ ] Gateway auth hardening (PROSPECTION_API_KEY rotation doc)

**E2E:** `OpenTicket/scripts/w4-e2e.sh` (partial) · prospection via OpenTeam
