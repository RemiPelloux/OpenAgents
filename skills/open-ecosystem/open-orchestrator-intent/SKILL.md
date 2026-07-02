---
name: open-orchestrator-intent
description: "Classify natural-language goals into NormalizedGoal JSON for OpenOrchestrator."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openorchestrator, intent, classifier, W4]
    related_skills: [open-orchestrator-plan, open-ecosystem-hub]
---

# Open Orchestrator Intent Classifier

Turns a user sentence into strict **NormalizedGoal** JSON for the control plane.

## When to Use

- OpenOrchestrator `POST /v1/goals` with **natural language only** (no hand JSON)
- Ticket webhook follow-up when objective text must be normalized before planning
- MCP `orch_submit_goal` when caller sends free-form text

## Prerequisites

- OpenOrchestrator API on `:3050` — schema at `GET /v1/schemas/openorchestrator.normalized-goal.v1`
- Profile: `intent_classifier` via `openagents openos init-profiles`
- Env on orchestrator: `INTENT_CLASSIFIER_AGENT_PROFILE=intent_classifier`

## Response contract

Return **JSON only** matching `openorchestrator.normalized-goal.v1`:

| Field | Purpose |
|-------|---------|
| `intent` | Routing intent slug (`ticket_fix`, `mission`, …) |
| `goal_class` | `engineering` \| `mission` \| `qa` \| `sales` \| `security` |
| `objective` | One imperative sentence |
| `risk_level` | 1–4; ≥3 when prod deploy, external email, merge, delete |
| `approval_required` | true when risk ≥ 3 |
| `ticket_key` | Extract `OP-42` style keys from the sentence |

## Procedure

1. Read `task_context.response_schema_url` when present
2. Parse the user sentence + optional brain/playbook context
3. Emit JSON only — no markdown fences
4. OpenOrchestrator validates with Zod + policy engine before planning

## Rules

- Never invent ticket IDs — only keys explicitly mentioned
- Do not decompose steps here — that is the **planner** profile (`open-orchestrator-plan`)
- Propagate `correlation_id` on every hop
