---
name: open-creative
description: Creative content workflows — OpenAI image generation and Brain deliverable handoff
---

# Open Creative

Use when OpenOrchestrator assigns creative tasks (`content.creative`) from OpenBrain workflows.

## Tools

- `generate_openai_images` — DALL-E via Brain vault `openai_api_key`
- `post_brain_deliverables` — push image URLs to OpenBrain chat for user approval

## Env

- `OPENBRAIN_URL` / `OPENBRAIN_API_URL`
- `OPENBRAIN_API_KEY` or `AXON_AGENT_API_KEY` (deliverable webhook)
- `INTERNAL_SERVICE_KEY` + `OPENBRAIN_ORG_ID` (secret resolve)
- Fallback: `OPENAI_API_KEY`
