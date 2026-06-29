---
name: open-whistle
description: "Use when deploying or integrating OpenWhistle — self-hosted HinSchG/EU whistleblower reporting and RemiPelloux SDKs."
version: 1.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openwhistle, compliance, hinschg, whistleblower, gdpr, fastapi, sdk]
    homepage: https://github.com/openwhistle/OpenWhistle
    related_skills: [open-ecosystem-hub, openagents]
---

# Open Whistle

**Open Whistle** is a self-hosted whistleblower reporting platform compliant with **HinSchG** (Germany) and **EU Directive 2019/1937**. Organizations with 50+ employees often need an internal reporting channel — OpenWhistle provides anonymity, bidirectional case messaging, and SLA tracking.

**SDKs:** [RemiPelloux/openwhistle-sdks](https://github.com/RemiPelloux/openwhistle-sdks) — JavaScript, Python, PHP, Rust, Go.

## When to use

- Deploying or hardening an OpenWhistle instance
- Integrating case management via official SDKs
- Automating admin workflows (with strict PII/anonymity rules)
- Legal/compliance questions about HinSchG deadlines (7-day ack, 3-month feedback)

Do **not** store whistleblower case content in general OpenAgents memory — see data boundaries below.

## Stack (upstream OpenWhistle)

| Component | Technology |
|-----------|------------|
| API | FastAPI + Python 3.14 |
| DB | PostgreSQL 18 (SQLAlchemy async) |
| Cache | Redis 8 |
| Auth | TOTP MFA (admins), case number + UUID PIN (reporters) |
| Proxy | nginx (IP logging disabled) |

## Key features

- **Full anonymity** — no application-level IP logging
- **Two-factor reporter access** — case ID + secret PIN, bruteforce protected
- **Bidirectional messaging** — HinSchG §17 compliant replies
- **SLA tracking** — 7-day acknowledgement, 3-month feedback deadlines
- **Attachments** — PDF, images, Office docs (size/count limits)
- **OIDC** — optional admin SSO
- **Setup wizard** — first-run admin + TOTP enrollment

## Deployment checklist

1. PostgreSQL + Redis provisioned (self-hosted)
2. `SECRET_KEY`, `DATABASE_URL`, Redis URL configured
3. nginx reverse proxy — **disable IP logging**; verify no upstream `X-Forwarded-For` leakage (`IP leakage detection` in app)
4. Admin MFA enforced (TOTP)
5. TLS everywhere; no external CDN for UI assets (DSGVO)
6. Backup strategy with encryption at rest

Use Docker images from ghcr.io / Docker Hub / quay.io per upstream docs.

## SDK usage (Python example)

Install from [openwhistle-sdks](https://github.com/RemiPelloux/openwhistle-sdks):

```python
# Pattern: use official client — do not hand-roll auth
from openwhistle_sdk import Client  # package name per SDK repo

client = Client(base_url="https://reports.example.com", api_token=os.environ["OPENWHISTLE_ADMIN_TOKEN"])
# Admin-only operations require MFA session — follow SDK auth flow
```

Always use SDK methods for case access — avoids auth mistakes and API drift.

## OpenAgents integration (guarded)

Agents may **assist admins** with non-identifying summaries only if explicitly requested:

| Allowed | Forbidden |
|---------|-----------|
| SLA deadline reminders (counts, dates) | Storing reporter PINs or case UUIDs in memory |
| Drafting generic acknowledgement templates | Pasting full report text into Slack/Telegram |
| SDK boilerplate for integrations | Logging attachment contents to trajectories |

Run gateway agents with `--yolo` **disabled** for any whistleblower-related host.

## Compliance reminders

- Mandatory internal channel for qualifying employers (DE/EU context)
- Hard deletion support for DSGVO erasure requests
- Document data processing agreement for your organization
- Legal review before customizing retention policies

## Common pitfalls

1. **IP leakage via proxy** — misconfigured nginx or CDN breaks anonymity promise
2. **Agent memory contamination** — never `/memory` store case details
3. **Skipping MFA** — admin accounts must use TOTP
4. **Custom API without SDK** — breaks when API versioning changes

## Verification checklist

- [ ] Reporter flow works without IP capture (inspect logs)
- [ ] Admin login requires TOTP
- [ ] SLA timers visible for open cases
- [ ] Attachments upload within configured limits
- [ ] SDK integration uses env-based secrets, not committed tokens
- [ ] OpenAgents (if used) has no whistleblower content in `~/.openagents/memory/`
