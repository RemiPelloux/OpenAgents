---
name: open-creative
description: "DALL-E images and Brain deliverable handoff."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [creative, images, openbrain, orchestrator]
    category: open-ecosystem
    related_skills: [open-brain-orchestrator, open-brain]
---

# Open Creative

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Creative deliverables from OpenBrain/orchestrator missions (`content.creative`).

## When to Use

- Orchestrator assigns image generation or creative asset task
- User approves visuals via Brain deliverable webhook
- **Not** for code tickets — use W4

## Prerequisites

- `OPENBRAIN_URL` / `OPENBRAIN_API_URL`
- `AXON_AGENT_API_KEY` or `OPENBRAIN_API_KEY`
- `OPENAI_API_KEY` or Brain vault `openai_api_key`
- Optional: `INTERNAL_SERVICE_KEY`, `OPENBRAIN_ORG_ID`

## Procedure

1. `generate_openai_images` — DALL-E via vault key
2. Review output URLs (no secrets in prompts)
3. `post_brain_deliverables` — push to Brain chat for human approval
4. Wait for approval before downstream publish

## Decision rules

| Task | Skill |
|------|-------|
| Marketing image | `open-creative` |
| Code change | `open-dev-workflow` |
| Doc update | `open-brain` ingest + sync |

## Pitfalls

- Publishing without human approval step
- PII in image prompts
- Using creative path for security/compliance content

## Verification

- [ ] Deliverable posted with retrievable URLs
- [ ] Approval recorded before external use
