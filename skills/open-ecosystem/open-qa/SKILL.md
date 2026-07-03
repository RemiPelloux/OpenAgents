---
name: open-qa
description: "QA sign-off: AC, tests, regression, integrator."
version: 1.0.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [QA, testing, acceptance, W4, sign-off]
    category: open-ecosystem
    related_skills: [openprotocol-integrator, open-ticket, open-code, open-rec, open-dev-workflow]
---

# Open QA (sign-off discipline)

**QA profile** owns acceptance verification and ticket closure — not implementation.

## When to Use

- Ticket `in_review` after coder handoff
- User asks for test plan, regression check, or sign-off
- Before `done` transition (QA only)

Pair with **`openprotocol-integrator`** for git merge; this skill covers **quality gates**.

## Prerequisites

- Handoff comment with branch name (`OpenProtocol handoff`)
- Ticket `acceptance_criteria[]` from `get_ticket`
- App DoD commands (test, typecheck, build)
- Optional: `invoke_opencode(mode=test|review)` for extra validation

## Procedure

1. **Read AC** — `get_ticket`; list each criterion as checkbox
2. **Read handoff** — branch, checks claimed, risk level
3. **Independent verify** — re-run full test suite (never trust handoff alone)
4. **AC map** — each criterion → evidence (test name, log, screenshot path)
5. **Regression** — `git diff origin/main...HEAD`; scope matches ticket only
6. **OpenSec CVE gate** — before push or merge, dependency audit must be clean at `high+`:
   ```bash
   # From app repo (runs automatically on git push via .husky/pre-push)
   bash ../scripts/opensec-pre-push-audit.sh

   # Full mesh audit (QA on integrator workstation)
   MESH_AUDIT_FAIL_ON=high bash ../scripts/mesh-security-audit.sh
   ```
   Block merge if `pnpm audit --audit-level=high` fails. Emergency only: `OPENSEC_SKIP_PRE_PUSH=1`.
7. **Security** — no secrets, no unrelated files
8. **Integrate** — `openprotocol-integrator` squash merge when all green
9. **Close** — `update_ticket_status` → `done` + comment with AC checklist + merge SHA
10. **Audit** — confirm `open-rec` trace for correlation_id

## AC verification template

```
QA sign-off OP-42
- [ ] AC1: <criterion> — evidence: <test/command output>
- [ ] AC2: …
- Branch: agent/OP-42/… merged at <sha>
- Regression: full suite green
```

## Decision rules

| AC fails | Action |
|----------|--------|
| Test red | Comment; reassign developer; stay `in_review` |
| Scope creep | Reject merge; request branch fix |
| Partial AC | Never `done` — list missing criteria |
| High/critical CVE | Reject push/merge; bump deps or document accepted risk in ticket |

| Risk in handoff | Extra step |
|-----------------|------------|
| medium/high | `invoke_opencode(mode=review)` + manual diff review |

## Pitfalls

- `done` without AC checklist
- Merging with failing tests
- QA implementing fixes on `main` (send back to developer branch)
- Skipping OpenRec correlation check
- Merging when `opensec-pre-push-audit.sh` fails (QA must not use `OPENSEC_SKIP_PRE_PUSH`)

## Verification

- [ ] Every AC has explicit evidence in ticket comment
- [ ] Full suite green on branch before merge
- [ ] OpenSec pre-push audit green (`high+` CVE threshold)
- [ ] Only QA profile set `done`
- [ ] `correlation_id` auditable in `open-rec`
