"""Canonical OpenOS agent profile catalog — 18 domain roles."""

from __future__ import annotations

from typing import Any, Dict, List

MCP_SERVER_DEFS: Dict[str, Dict[str, Any]] = {
    "openticket": {
        "cwd": "${OPENOS_ROOT}/OpenTicket",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
    },
    "openorchestrator": {
        "cwd": "${OPENOS_ROOT}/OpenOrchestrator",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
        "env": {"ORCHESTRATOR_URL": "http://localhost:3050"},
    },
    "opencrm": {
        "cwd": "${OPENOS_ROOT}/OpenCRM",
        "args": ["exec", "tsx", "apps/mcp-server/src/index.ts"],
    },
}

PROFILE_SPECS: Dict[str, Dict[str, Any]] = {
    "planner": {
        "description": "Planner — decomposes objectives into orchestrated steps",
        "toolsets": ["delegation", "mcp"],
        "skills": [
            "open-orchestrator-plan",
            "open-orchestrator-ops",
            "open-ecosystem-hub",
            "open-ticket-optimize",
        ],
        "mcp_servers": ["openorchestrator", "openticket"],
        "soul": (
            "You are the OpenOrchestrator planner. Decompose objectives with "
            "open-orchestrator-ops gates. Return JSON only when decomposing."
        ),
    },
    "intent_classifier": {
        "description": "Intent classifier — NL → NormalizedGoal JSON",
        "toolsets": ["mcp"],
        "skills": ["open-orchestrator-intent", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You are the intent classifier. Parse NL into strict NormalizedGoal "
            "JSON. Return JSON only — no markdown."
        ),
    },
    "skill_author": {
        "description": "Skill Author — org skill patches on mesh gaps",
        "toolsets": ["skills", "mcp"],
        "skills": [
            "open-orchestrator-plan",
            "open-orchestrator-ops",
            "open-mesh-wiring",
            "open-mcp-scaffold",
            "open-brain",
        ],
        "mcp_servers": ["openorchestrator"],
        "soul": (
            "You author org-scoped skills and profiles when gaps are reported. "
            "Never modify bundled OpenOS skills — org overlay only."
        ),
    },
    "product_owner": {
        "description": "Product Owner — tickets and acceptance criteria",
        "toolsets": ["delegation", "mcp"],
        "skills": [
            "open-ticket",
            "open-ticket-optimize",
            "open-dev-workflow",
            "open-contract",
            "open-ecosystem-hub",
        ],
        "mcp_servers": ["openticket", "openorchestrator"],
        "soul": (
            "You are the Product Owner. Create and refine OpenTicket stories "
            "with clear AC. Do not write code — delegate to developer."
        ),
    },
    "developer": {
        "description": "Developer — implements tickets via OpenCode",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": [
            "open-code",
            "open-ticket",
            "open-dev-workflow",
            "open-mesh-wiring",
            "openprotocol-coder",
            "open-generic",
        ],
        "mcp_servers": ["openticket"],
        "soul": (
            "You are the Developer. Load openprotocol-coder, invoke_opencode "
            "for all code, push agent branch, hand off to QA."
        ),
    },
    "qa": {
        "description": "QA integrator — verify and squash merge",
        "toolsets": ["terminal", "mcp", "openos_engineering"],
        "skills": [
            "open-code",
            "open-ticket",
            "open-dev-workflow",
            "open-qa",
            "openprotocol-integrator",
            "open-generic",
        ],
        "mcp_servers": ["openticket"],
        "soul": (
            "You are QA. Load open-qa for AC sign-off, then openprotocol-integrator "
            "to squash merge main. Only you may set tickets to done."
        ),
    },
    "security": {
        "description": "Security analyst — findings to tickets",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-sec", "open-ticket", "open-contract", "open-ecosystem-hub", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": "You triage OpenSec findings into OpenTicket with correlation IDs. No prod deploys.",
    },
    "sales": {
        "description": "Sales — CRM follow-up and outreach",
        "toolsets": ["delegation", "mcp"],
        "skills": ["opencrm-sales-followup", "open-creative", "open-generic", "open-ecosystem-hub"],
        "mcp_servers": ["opencrm"],
        "soul": "You run staged CRM updates and outreach drafts. External sends need approval.",
    },
    "creative": {
        "description": "Creative — image and prompt deliverables",
        "toolsets": ["delegation"],
        "skills": ["open-creative", "open-brain", "open-generic"],
        "mcp_servers": [],
        "soul": "You craft creative prompts and deliver assets. Document outputs for Brain ingest.",
    },
    "crm_analyst": {
        "description": "CRM analyst — accounts, leads, context",
        "toolsets": ["mcp"],
        "skills": ["opencrm-sales-followup", "open-brain", "open-generic"],
        "mcp_servers": ["opencrm"],
        "soul": "You analyze CRM data and propose staged updates — never direct client email without approval.",
    },
    "brain_researcher": {
        "description": "Brain researcher — RAG, missions, docs",
        "toolsets": ["mcp"],
        "skills": ["open-brain", "open-brain-orchestrator", "open-generic", "open-ecosystem-hub"],
        "mcp_servers": ["openorchestrator"],
        "soul": "You query OpenBrain for validated context. Cite domain:openos for mesh specs.",
    },
    "mesh_engineer": {
        "description": "Mesh engineer — CC-*, wiring, MCP scaffold",
        "toolsets": ["delegation", "terminal", "mcp", "openos_engineering"],
        "skills": [
            "open-mesh-wiring",
            "open-contract",
            "open-mcp-scaffold",
            "open-rec",
            "open-generic",
        ],
        "mcp_servers": ["openorchestrator", "openticket"],
        "soul": "You wire mesh hops: register CC-* first, envelope on every hop, MCP+REST parity.",
    },
    "notes_analyst": {
        "description": "Notes analyst — meetings and audio intelligence",
        "toolsets": ["mcp"],
        "skills": ["open-notes", "open-brain", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": "You structure meeting intelligence and emit Brain observations — no PII in summaries.",
    },
    "content_ops": {
        "description": "Content ops — harvest and dispatch",
        "toolsets": ["delegation", "mcp"],
        "skills": ["open-team", "open-creative", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": "You run OpenTeam harvest workflows and hand off creative or ticket tasks.",
    },
    "compliance_officer": {
        "description": "Compliance — whistleblower and policy intake",
        "toolsets": ["mcp"],
        "skills": ["open-whistle", "open-sec", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": "You handle compliance intake with strict PII boundaries. Escalate via tickets only.",
    },
    "recorder_analyst": {
        "description": "Recorder analyst — audit trace and correlation",
        "toolsets": ["mcp"],
        "skills": ["open-rec", "open-brain", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": "You trace RecEvents and correlate mesh hops. Summaries for Brain — no secrets.",
    },
    "contract_officer": {
        "description": "Contract officer — CC-* registry and envelopes",
        "toolsets": ["mcp"],
        "skills": ["open-contract", "open-mesh-wiring", "open-ecosystem-hub", "open-generic"],
        "mcp_servers": ["openorchestrator"],
        "soul": "You own OpenContract step 0. No hop ships without CC-* and verified envelope.",
    },
    "mobile_engineer": {
        "description": "Mobile engineer — OpenPro Flutter",
        "toolsets": ["delegation", "terminal", "openos_engineering"],
        "skills": ["open-pro", "open-code", "open-dev-workflow", "openprotocol-coder", "open-generic"],
        "mcp_servers": ["openticket"],
        "soul": "You implement OpenPro mobile changes via invoke_opencode on agent branches.",
    },
}


def list_profile_ids() -> List[str]:
    return sorted(PROFILE_SPECS.keys())


def get_profile_spec(profile_id: str) -> Dict[str, Any] | None:
    return PROFILE_SPECS.get(profile_id)
