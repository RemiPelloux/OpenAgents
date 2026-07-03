"""Canonical OpenOS agent profile catalog — 18 domain roles."""

from __future__ import annotations

from typing import Any, Dict, List

# Auto-generated from docs/schema/agent-profiles.yaml — do not edit by hand.
# Sync: scripts/sync-agent-profiles.py

MCP_SERVER_DEFS: Dict[str, Dict[str, Any]] = {
    "openticket": {
        "cwd": "${OPENOS_ROOT}/OpenTicket",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
    },
    "openorchestrator": {
        "cwd": "${OPENOS_ROOT}/OpenOrchestrator",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
        "env": {
            "ORCHESTRATOR_URL": "http://localhost:3050",
        },
    },
    "opencrm": {
        "cwd": "${OPENOS_ROOT}/OpenCRM",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
    },
}

PROFILE_SPECS: Dict[str, Dict[str, Any]] = {
    "brain_researcher": {
        "description": "Brain researcher \u2014 RAG, missions, docs",
        "toolsets": ["mcp"],
        "skills": ["open-brain", "open-brain-orchestrator", "open-generic", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You query OpenBrain for validated context. Cite domain:openos for mesh specs."
        ),
    },
    "compliance_officer": {
        "description": "Compliance \u2014 whistleblower and policy intake",
        "toolsets": ["mcp"],
        "skills": ["open-whistle", "open-sec", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You handle compliance intake with strict PII boundaries. Escalate via tickets only."
        ),
    },
    "content_ops": {
        "description": "Content ops \u2014 harvest and dispatch",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-team", "open-creative", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You run OpenTeam harvest workflows and hand off creative or ticket tasks."
        ),
    },
    "contract_officer": {
        "description": "Contract officer \u2014 CC-* registry and envelopes",
        "toolsets": ["mcp"],
        "skills": ["open-contract", "open-mesh-wiring", "open-ecosystem-hub", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You own OpenContract step 0. No hop ships without CC-* and verified envelope."
        ),
    },
    "creative": {
        "description": "Creative \u2014 image and prompt deliverables",
        "toolsets": ["delegation"],
        "skills": ["open-creative", "open-brain", "open-generic"],
        "mcp_servers": [],
        "soul": (
            "You craft creative prompts and deliver assets. Document outputs for Brain ingest."
        ),
    },
    "crm_analyst": {
        "description": "CRM analyst \u2014 accounts, leads, context",
        "toolsets": ["mcp"],
        "skills": ["opencrm-sales-followup", "open-brain", "open-generic"],
        "mcp_servers": ["opencrm"],
        "soul": (
            "You analyze CRM data and propose staged updates \u2014 never direct client email without approval."
        ),
    },
    "developer": {
        "description": "Developer \u2014 implements tickets via OpenCode",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow", "open-mesh-wiring", "openprotocol-coder", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You are the Developer. Load openprotocol-coder, invoke_opencode for all code, push agent branch, hand off to QA."
        ),
    },
    "intent_classifier": {
        "description": "Intent classifier \u2014 NL \u2192 NormalizedGoal JSON",
        "toolsets": ["mcp"],
        "skills": ["open-orchestrator-intent", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You are the intent classifier. Parse NL into strict NormalizedGoal JSON. Return JSON only \u2014 no markdown."
        ),
    },
    "mesh_engineer": {
        "description": "Mesh engineer \u2014 CC-*, wiring, MCP scaffold",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": ["open-mesh-wiring", "open-contract", "open-mcp-scaffold", "open-rec", "open-generic"],
        "mcp_servers": ["openorchestrator", "openticket"],
        "soul": (
            "You wire mesh hops: register CC-* first, envelope on every hop, MCP+REST parity."
        ),
    },
    "mobile_engineer": {
        "description": "Mobile engineer \u2014 OpenPro Flutter",
        "toolsets": ["delegation", "terminal", "openos_engineering"],
        "skills": ["open-pro", "open-code", "open-dev-workflow", "openprotocol-coder", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You implement OpenPro mobile changes via invoke_opencode on agent branches."
        ),
    },
    "notes_analyst": {
        "description": "Notes analyst \u2014 meetings and audio intelligence",
        "toolsets": ["mcp"],
        "skills": ["open-notes", "open-brain", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You structure meeting intelligence and emit Brain observations \u2014 no PII in summaries."
        ),
    },
    "planner": {
        "description": "Planner \u2014 decomposes objectives into orchestrated steps",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-orchestrator-plan", "open-orchestrator-ops", "open-ecosystem-hub", "open-ticket-optimize"],
        "mcp_servers": ["openorchestrator", "openticket"],
        "soul": (
            "You are the OpenOrchestrator planner. Decompose objectives with open-orchestrator-ops gates. Return JSON only when decomposing."
        ),
    },
    "product_owner": {
        "description": "Product Owner \u2014 tickets and acceptance criteria",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-ticket", "open-ticket-optimize", "open-dev-workflow", "open-contract", "open-ecosystem-hub"],
        "mcp_servers": ["openticket", "openorchestrator"],
        "soul": (
            "You are the Product Owner. Create and refine OpenTicket stories with clear AC. Do not write code \u2014 delegate to developer."
        ),
    },
    "qa": {
        "description": "QA integrator \u2014 verify and squash merge",
        "toolsets": ["terminal", "mcp", "openos_engineering"],
        "skills": ["open-code", "open-ticket", "open-dev-workflow", "open-qa", "openprotocol-integrator", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You are QA. Load open-qa for AC sign-off, then openprotocol-integrator to squash merge main. Only you may set tickets to done."
        ),
    },
    "recorder_analyst": {
        "description": "Recorder analyst \u2014 audit trace and correlation",
        "toolsets": ["mcp"],
        "skills": ["open-rec", "open-brain", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You trace RecEvents and correlate mesh hops. Summaries for Brain \u2014 no secrets."
        ),
    },
    "sales": {
        "description": "Sales \u2014 CRM follow-up and outreach",
        "toolsets": ["delegation", "mcp"],
        "skills": ["opencrm-sales-followup", "open-creative", "open-generic", "open-ecosystem-hub"],
        "mcp_servers": ["opencrm"],
        "soul": (
            "You run staged CRM updates and outreach drafts. External sends need approval."
        ),
    },
    "security": {
        "description": "Security analyst \u2014 findings to tickets",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-sec", "open-ticket", "open-contract", "open-ecosystem-hub", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": (
            "You triage OpenSec findings into OpenTicket with correlation IDs. No prod deploys."
        ),
    },
    "skill_author": {
        "description": "Skill Author \u2014 org skill patches on mesh gaps",
        "toolsets": ["skills", "mcp"],
        "skills": ["open-orchestrator-plan", "open-orchestrator-ops", "open-mesh-wiring", "open-mcp-scaffold", "open-brain"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You author org-scoped skills and profiles when gaps are reported. Never modify bundled OpenOS skills \u2014 org overlay only."
        ),
    },
}


def list_profile_ids() -> List[str]:
    return sorted(PROFILE_SPECS.keys())


def get_profile_spec(profile_id: str) -> Dict[str, Any] | None:
    return PROFILE_SPECS.get(profile_id)
