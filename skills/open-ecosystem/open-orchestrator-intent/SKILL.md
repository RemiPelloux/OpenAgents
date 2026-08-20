---
name: open-orchestrator-intent
description: "NL goals to NormalizedGoal JSON for orchestrator."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openorchestrator, intent, classifier]
    category: open-ecosystem
    related_skills: [open-orchestrator-plan, open-dev-workflow]
---

# Open Orchestrator Intent

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Classifies free text into **NormalizedGoal** JSON — no planning here.

## When to Use

- `POST /v1/goals` with natural language only
- `intent_classifier` profile invoked
- Pre-plan normalization when objective is unstructured

## Prerequisites

- Orchestrator `:3050` — schema `GET /v1/schemas/openorchestrator.normalized-goal.v1`
- Profile `intent_classifier` from `openagents openos init-profiles`
- `INTENT_CLASSIFIER_AGENT_PROFILE=intent_classifier` on orchestrator

## Procedure

1. Read `task_context.response_schema_url` if present
2. Parse user sentence + optional brain context
3. Emit **JSON only** (no markdown fences)
4. Orchestrator validates with Zod + policy before plan step

## Response fields

| Field | Notes |
|-------|-------|
| `intent` | Slug e.g. `ticket_fix`, `mission` |
| `goal_class` | `engineering` \| `mission` \| `qa` \| `sales` \| `security` |
| `objective` | One imperative sentence |
| `risk_level` | 1–4; ≥3 → `approval_required` |
| `ticket_key` | Only if explicitly in input (`OP-42`) |

## Decision rules

| Input mentions | Set |
|----------------|-----|
| Deploy prod / delete / external email | `risk_level` ≥ 3 |
| Ticket key in text | `ticket_key` field |
| Multi-step work | Stop here — planner decomposes (`open-orchestrator-plan`) |

## Pitfalls

- Inventing ticket IDs not in the sentence
- Decomposing steps (planner's job)
- Markdown-wrapped JSON

## Verification

- [ ] Output parses as `openorchestrator.normalized-goal.v1`
- [ ] `approval_required` true when risk ≥ 3
- [ ] `correlation_id` propagated on next hop
