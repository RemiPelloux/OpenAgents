---
name: open-sec
description: "AGI security monitor — observe findings, alert, and open OpenTicket bugs. OpenSec :3040 is not required."
version: 2.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [security, AGI, findings, OpenTicket, mesh]
    category: open-ecosystem
    related_skills: [open-ticket, open-brain, open-rec]
---

# Security (AGI monitor)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

OpenBrain AGI watches security signals. Do **not** call OpenSec.

Canonical skill: `OpenBrain/skills/openos-security-monitor/SKILL.md`.

## When to Use

- User asks to scan, escalate, or investigate an incident
- A secret was blocked from memory
- An assistant job failed or was blocked

## Procedure

1. Use Brain tools `security_scan`, `escalate_finding`, or `incident_autopilot`
2. High/critical → OpenTicket `bug` assigned to `security`
3. Medium+ → in-app alert + RecEvent `brain.security.finding`
4. Never store raw secrets in tickets or observations

## Verification

- [ ] Finding fingerprint is stable (`org + kind + key`)
- [ ] Ticket metadata includes `finding_id`
- [ ] OpenSec `:3040` was not called
