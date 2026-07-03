---
name: open-whistle
description: "HinSchG whistleblower deploy and SDK integration."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openwhistle, compliance, hinschg, sdk]
    category: open-ecosystem
    related_skills: [open-ecosystem-hub, open-contract]
---

# Open Whistle

Self-hosted whistleblower channel (HinSchG / EU 2019/1937). SDKs: `openwhistle-sdks`.

## When to Use

- Deploy/harden OpenWhistle instance
- Integrate case APIs via official SDKs
- SLA/legal workflow (7-day ack, 3-month feedback)

**Never** store case content in general agent memory.

## Prerequisites

- PostgreSQL + Redis for upstream app
- `SECRET_KEY`, `DATABASE_URL`, TLS
- nginx with **IP logging disabled**

## Structural overview

| Actor | Auth |
|-------|------|
| Reporter | Case ID + UUID PIN |
| Admin | TOTP MFA (+ optional OIDC) |

Stack: FastAPI, async SQLAlchemy, Redis.

## Procedure — deploy

1. Provision DB + Redis
2. Configure secrets; enforce admin MFA
3. nginx: no IP leakage; verify app leakage detection
4. TLS end-to-end; backups encrypted
5. Integrate via SDK only — not scraped HTML

## Procedure — agent assist (triage)

1. Redact all reporter identity from agent context
2. Use SDK read APIs for case metadata only
3. Never `ingest_observation` with raw report body

## Decision rules

| Task | Path |
|------|------|
| Legal intake | OpenWhistle app |
| General agent memory | Forbidden for case bodies |
| Automation | SDK + explicit human gate |

## Pitfalls

- IP logging at proxy
- Case PII in Brain/OpenAgents memory
- Skipping MFA on admin accounts

## Verification

- [ ] Reporter path works without identity collection
- [ ] SLA timers visible in admin UI
- [ ] SDK integration uses official package versions
