---
name: open-contract
description: "Register CC-* hops and sign ContractEnvelope payloads."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [OpenContract, CC, envelope, mesh, step0]
    category: open-ecosystem
    related_skills: [open-dev-workflow, open-rec, open-brain, open-mcp-scaffold]
---

# OpenContract (mesh step 0)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Every inter-app hop needs a declared `CC-*` contract and a signed **ContractEnvelope**.
OpenContract is the registry + validator — not a business app.

## When to Use

- Adding any producer → consumer edge (MCP, REST, webhook, RecEvent)
- Debugging `blocked` or signature failures on mesh hops
- Before implementing OpenCode session-complete or ticket webhooks

Do **not** use for Brain doc RAG alone — see `open-brain` (`CC-BRAIN-*` is ingest, not transport).

## Structural overview

| Object | Location |
|--------|----------|
| Registry YAML | `OpenContract/registry/*.yaml` |
| Identities | `OpenContract/registry/identities.yaml` |
| TS SDK | `@opencontract/envelope` (`wrapSignedIfProducer`, `parseInboundHop`) |
| API | `:3070` validate/wrap |
| Handbook | `OpenOS/docs/opencontract/OPENCONTRACT-HANDBOOK.md` |

Lifecycle: `draft` → `implemented` → `verified` (E2E + correlation assert).

## Prerequisites

- Registry row exists **before** code ships
- Env: `OPENCONTRACT_IDENTITY`, `OPENCONTRACT_SIGNING_KEY` (or `OPENCONTRACT_DEV_KEYS=1` local)
- Strict prod: `OPENCONTRACT_REQUIRE_SIGNATURE=1`

## Procedure

1. **Declare** — add `CC-*` row in `OpenContract/registry/` (producer, consumer, plane, payload schema)
2. **Implement** — producer signs outbound; consumer verifies inbound
3. **Propagate** — `X-Correlation-Id` on every hop
4. **Verify** — E2E script asserts goal + correlation; mark `verified` in registry

Producer (TS):

```typescript
import { wrapSignedIfProducer } from '@opencontract/envelope'
return wrapSignedIfProducer({ contractId, correlationId, producer, consumer, payload })
```

Consumer:

```typescript
import { parseInboundHop } from '@opencontract/envelope'
await parseInboundHop(body, contractId, (p) => schema.parse(p))
```

## Decision rules

| Situation | Action |
|-----------|--------|
| New mesh edge | Register `CC-*` first, then code |
| Consumer HTTP response | Ack hop — do not sign as another app's identity |
| Multiple hops in one flow | One `CC-*` per hop — no merged blobs |
| Local dev | `OPENCONTRACT_DEV_KEYS=1` + OpenContract API on :3070 |

## Pitfalls

- Shipping REST/MCP without registry row
- Plain JSON on producer outbound (must be envelope when contract-owned)
- Skipping E2E envelope assertions
- Fake producer identity on consumer endpoints

## Verification

- [ ] `opencontract list` shows contract `implemented` or `verified`
- [ ] Producer outbound includes `contract_id` + signature
- [ ] Consumer rejects tampered payload
- [ ] E2E script passes with `OPENCONTRACT_REQUIRE_SIGNATURE=1`
