"""Shared ``/company`` command — scaffold multi-agent company workspaces.

Creates a folder layout optimized for OpenAgents delegation: manifest,
role definitions, per-role agent configs (SOUL + toolsets), skills map, and
a playbook the CEO/orchestrator reads from ``AGENTS.md``.

Subcommands::

  /company                         help + status when inside a company folder
  /company init <name> [path]      scaffold a new company (default path: ./<slug>)
  /company status                  show manifest + roles for cwd company
  /company roles [role-id]         list roles or show one role
  /company delegate <role> <goal>  seed the agent to run work as that role
  /company spawn <role> <goal>     alias for delegate
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import shlex
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

MANIFEST_NAME = "company.yaml"
COMPANY_PLAYBOOK = "COMPANY.md"
AGENTS_GUIDE = "AGENTS.md"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


@dataclass
class CompanyCommandResult:
    """Outcome of a ``/company`` invocation."""

    text: str
    agent_seed: Optional[str] = None


@dataclass(frozen=True)
class RoleTemplate:
    role_id: str
    title: str
    delegate_role: str  # leaf | orchestrator
    toolsets: tuple[str, ...]
    skills: tuple[str, ...]
    soul: str
    focus: str


# ---------------------------------------------------------------------------
# Role templates (startup = balanced product team)
# ---------------------------------------------------------------------------

_STARTUP_ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "ceo",
        "Chief Executive (Orchestrator)",
        "orchestrator",
        ("delegation", "file", "terminal", "web", "todo", "kanban"),
        ("subagent-driven-development", "plan"),
        "You are the CEO of this company workspace. You coordinate specialists, "
        "break goals into parallel workstreams, and delegate via delegate_task. "
        "You do not implement code or research yourself — you route, review, and synthesize.",
        "Planning, delegation, synthesis, and quality gates.",
    ),
    RoleTemplate(
        "engineer",
        "Software Engineer",
        "leaf",
        ("file", "terminal", "debugging", "web"),
        ("test-driven-development", "requesting-code-review"),
        "You are the engineering lead for this company. Ship correct, tested code "
        "in the workspace. Prefer small diffs, run tests, and report files changed.",
        "Implementation, refactors, debugging, and test coverage.",
    ),
    RoleTemplate(
        "researcher",
        "Research Analyst",
        "leaf",
        ("web", "file", "browser"),
        ("parallel-cli", "duckduckgo-search"),
        "You are the research analyst. Gather facts, compare sources, and deliver "
        "structured briefs with citations. Do not speculate beyond evidence.",
        "Market research, competitive analysis, and fact-finding.",
    ),
    RoleTemplate(
        "writer",
        "Content & Comms",
        "leaf",
        ("file", "web"),
        ("humanizer", "one-three-one-rule"),
        "You are the writer for this company. Produce clear docs, copy, and "
        "user-facing text that matches the company voice in COMPANY.md.",
        "Documentation, messaging, and editorial polish.",
    ),
    RoleTemplate(
        "ops",
        "Operations & DevOps",
        "leaf",
        ("terminal", "file", "cronjob"),
        ("cli", "docker-management"),
        "You are operations. Automate repeatable workflows, keep environments "
        "healthy, and wire cron/infra tasks. Prefer idempotent scripts.",
        "CI hooks, cron jobs, Docker, and release automation.",
    ),
)

_STUDIO_ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "ceo",
        "Creative Director (Orchestrator)",
        "orchestrator",
        ("delegation", "file", "web", "todo", "kanban"),
        ("subagent-driven-development", "plan"),
        "You lead the studio. Brief specialists, review deliverables, and keep "
        "creative work aligned with the mission in COMPANY.md.",
        "Creative direction, delegation, and final review.",
    ),
    RoleTemplate(
        "designer",
        "Product Designer",
        "leaf",
        ("file", "web", "browser"),
        ("canvas", "meme-generation"),
        "You own UX, visual design, and prototypes. Document decisions in workspace/.",
        "Wireframes, visual specs, and design systems.",
    ),
    RoleTemplate(
        "engineer",
        "Implementing Engineer",
        "leaf",
        ("file", "terminal", "debugging"),
        ("test-driven-development",),
        "You turn specs into working software. Test before you report done.",
        "Frontend/backend implementation.",
    ),
    RoleTemplate(
        "qa",
        "Quality Assurance",
        "leaf",
        ("file", "terminal", "browser"),
        ("test-driven-development",),
        "You verify acceptance criteria, reproduce bugs, and sign off releases.",
        "Test plans, regression checks, and release notes.",
    ),
)

_MINIMAL_ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "ceo",
        "Orchestrator",
        "orchestrator",
        ("delegation", "file", "terminal", "web", "todo"),
        ("subagent-driven-development",),
        "You coordinate work and delegate to the worker role.",
        "Delegation and review.",
    ),
    RoleTemplate(
        "worker",
        "Generalist Worker",
        "leaf",
        ("file", "terminal", "web"),
        (),
        "You execute assigned tasks end-to-end and report summaries.",
        "General implementation and research.",
    ),
)

_OPENPRO_ENGINEERING_ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "engineering_orchestrator",
        "Engineering Orchestrator",
        "orchestrator",
        ("delegation", "mcp", "todo"),
        ("open-dev-workflow", "open-ticket"),
        "You coordinate the W4 engineering loop: PO creates tickets, Dev implements "
        "via OpenCode, QA validates. Delegate via delegate_task; never write code yourself.",
        "W4 routing, assignment, and synthesis.",
    ),
    RoleTemplate(
        "product_owner",
        "Product Owner",
        "leaf",
        ("delegation", "mcp"),
        ("open-ticket", "open-dev-workflow"),
        "You are the Product Owner. Create OpenTicket stories with clear "
        "acceptance criteria. Transition tickets to todo for developer pickup. "
        "You do not write code.",
        "Backlog, stories, acceptance criteria, ticket refinement.",
    ),
    RoleTemplate(
        "developer",
        "Developer",
        "leaf",
        ("delegation", "terminal", "mcp", "openos_engineering"),
        ("open-code", "open-ticket", "open-dev-workflow"),
        "You are the Developer. Read assigned tickets, use invoke_opencode for "
        "all code changes, and move tickets to in_progress. Never close to done.",
        "Implementation via OpenCode, ticket status updates.",
    ),
    RoleTemplate(
        "qa",
        "Quality Assurance",
        "leaf",
        ("terminal", "mcp", "openos_engineering"),
        ("open-code", "open-ticket", "open-dev-workflow"),
        "You are QA. Verify acceptance criteria with invoke_opencode review/test "
        "mode. Only you may transition tickets from qa to done.",
        "Test plans, regression, ticket sign-off.",
    ),
)

TEMPLATES: Dict[str, tuple[RoleTemplate, ...]] = {
    "startup": _STARTUP_ROLES,
    "studio": _STUDIO_ROLES,
    "minimal": _MINIMAL_ROLES,
    "openpro-engineering": _OPENPRO_ENGINEERING_ROLES,
}


def _slugify(name: str) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-_")
    return (s[:64].strip("-_") or "company")


def _normalize_slug(slug: str) -> str:
    s = str(slug or "").strip().lower()
    if not s or not _SLUG_RE.match(s):
        raise ValueError(
            f"invalid slug {slug!r}: use lowercase letters, digits, hyphens (1-64 chars)"
        )
    return s


def find_company_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (or cwd) for ``company.yaml``."""
    cur = Path(start or os.getcwd()).resolve()
    for directory in (cur, *cur.parents):
        if (directory / MANIFEST_NAME).is_file():
            return directory
    return None


def load_manifest(root: Path) -> Dict[str, Any]:
    path = root / MANIFEST_NAME
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{MANIFEST_NAME} must be a mapping")
    return data


def _role_from_manifest(manifest: Dict[str, Any], role_id: str) -> Optional[Dict[str, Any]]:
    roles = manifest.get("roles") or []
    if not isinstance(roles, list):
        return None
    for entry in roles:
        if isinstance(entry, dict) and str(entry.get("id", "")).lower() == role_id.lower():
            return entry
    return None


def _list_role_ids(manifest: Dict[str, Any]) -> List[str]:
    roles = manifest.get("roles") or []
    ids: List[str] = []
    for entry in roles:
        if isinstance(entry, dict) and entry.get("id"):
            ids.append(str(entry["id"]))
    return ids


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _role_yaml(role: RoleTemplate) -> Dict[str, Any]:
    return {
        "id": role.role_id,
        "title": role.title,
        "delegate_role": role.delegate_role,
        "toolsets": list(role.toolsets),
        "skills": list(role.skills),
        "focus": role.focus,
    }


def _agent_yaml(role: RoleTemplate) -> Dict[str, Any]:
    return {
        "role_id": role.role_id,
        "title": role.title,
        "delegate_role": role.delegate_role,
        "toolsets": list(role.toolsets),
        "skills": list(role.skills),
        "model_hint": "",
        "notes": role.focus,
    }


def _company_playbook(name: str, mission: str, roles: tuple[RoleTemplate, ...]) -> str:
    role_lines = "\n".join(
        f"- **{r.role_id}** — {r.title}: {r.focus}" for r in roles
    )
    return textwrap.dedent(
        f"""\
        # {name}

        {mission or "Define your mission here."}

        ## How this company works

        OpenAgents treats this folder as a **multi-agent company**. The manifest
        lives in `{MANIFEST_NAME}`. Each role has:

        - `roles/<id>.yaml` — toolsets, skills, delegation mode
        - `agents/<id>/SOUL.md` — persona for subagents
        - `agents/<id>/agent.yaml` — machine-readable config for `/company delegate`

        ## Roles

        {role_lines}

        ## Daily workflow

        1. **CEO / orchestrator** — `/company delegate ceo <goal>` or ask in chat to plan and fan out.
        2. **Specialists** — delegate with `delegate_task(goal=..., context=..., toolsets=[...], role='leaf')`.
        3. **Parallel batch** — `delegate_task(tasks=[{{...}}, {{...}}])` for concurrent roles.
        4. **Kanban** — run `/kanban init {name}` then link `kanban.board_slug` in `{MANIFEST_NAME}`.

        ## Skills

        Install recommended skills with `/skills search <name>` then `/skills install ...`.
        Role → skill mapping is in `skills/assignments.yaml`.

        ## Workspace

        Put deliverables in `workspace/`. Shared docs go in `docs/`.
        """
    )


def _agents_guide(name: str) -> str:
    return textwrap.dedent(
        f"""\
        # {name} — Agent guide

        This file is loaded automatically when you work inside this company folder.

        - Read `{COMPANY_PLAYBOOK}` for mission, roles, and workflow.
        - Use `/company roles` to inspect role toolsets and skills.
        - Use `/company delegate <role> <goal>` to spawn work as a specialist.
        - Prefer `delegate_task` with explicit `context` — subagents start with zero history.
        - Keep orchestration in the CEO role; leaf roles implement, research, or write.
        """
    )


def _assignments_yaml(roles: tuple[RoleTemplate, ...]) -> Dict[str, Any]:
    return {
        "description": "Recommended skills per role (install via /skills)",
        "roles": {
            r.role_id: {
                "skills": list(r.skills),
                "optional_skills": [],
            }
            for r in roles
        },
    }


def _resolve_template_roles(
    template: str,
    *,
    role_ids: Optional[List[str]] = None,
) -> tuple[RoleTemplate, ...]:
    """Return role templates for a template, optionally filtered by id."""
    base = TEMPLATES.get(template) or TEMPLATES["startup"]
    if not role_ids:
        return base
    wanted = {str(r).strip().lower() for r in role_ids if str(r).strip()}
    picked = tuple(r for r in base if r.role_id in wanted)
    if not picked:
        raise ValueError(
            f"no roles matched {role_ids!r} for template {template!r}; "
            f"available: {[r.role_id for r in base]}"
        )
    if "ceo" not in {r.role_id for r in picked}:
        ceo = next((r for r in base if r.role_id == "ceo"), None)
        if ceo is not None:
            picked = (ceo,) + picked
    return picked


def _parse_role_ids(raw: str) -> Optional[List[str]]:
    text = (raw or "").strip()
    if not text:
        return None
    return [part.strip().lower() for part in text.split(",") if part.strip()]


def apply_company_init(
    *,
    name: str,
    path: str,
    template: str = "startup",
    mission: str = "",
    role_ids: Optional[List[str]] = None,
    register_project: bool = True,
) -> Path:
    """Create a company workspace (CLI + agent apply entry point)."""
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = Path(os.getcwd()) / target
    roles = _resolve_template_roles(template, role_ids=role_ids)
    root = target
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"target not empty: {root}")

    slug = _slugify(name)
    if template not in TEMPLATES:
        logger.warning("Unknown company template %r, using startup", template)
        template = "startup"
        roles = _resolve_template_roles(template, role_ids=role_ids)

    manifest: Dict[str, Any] = {
        "version": 1,
        "name": name.strip() or slug,
        "slug": slug,
        "template": template,
        "mission": mission.strip()
        or f"Build and ship outcomes for {name.strip() or slug}.",
        "created_at": int(time.time()),
        "default_role": "ceo",
        "roles": [_role_yaml(r) for r in roles],
        "kanban": {"board_slug": ""},
        "delegation": {"max_concurrent_children": 3},
    }

    root.mkdir(parents=True, exist_ok=True)
    _write_text(root / MANIFEST_NAME, yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    _write_text(root / COMPANY_PLAYBOOK, _company_playbook(name, manifest["mission"], roles))
    _write_text(root / AGENTS_GUIDE, _agents_guide(manifest["name"]))

    for role in roles:
        _write_text(
            root / "roles" / f"{role.role_id}.yaml",
            yaml.safe_dump(_role_yaml(role), sort_keys=False, allow_unicode=True),
        )
        _write_text(root / "agents" / role.role_id / "SOUL.md", role.soul + "\n")
        _write_text(
            root / "agents" / role.role_id / "agent.yaml",
            yaml.safe_dump(_agent_yaml(role), sort_keys=False, allow_unicode=True),
        )

    _write_text(
        root / "skills" / "assignments.yaml",
        yaml.safe_dump(_assignments_yaml(roles), sort_keys=False, allow_unicode=True),
    )
    _write_text(
        root / "skills" / "README.md",
        "Install skills listed in assignments.yaml via `/skills search` and `/skills install`.\n",
    )
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    _write_text(root / "workspace" / ".gitkeep", "")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    _write_text(
        root / "docs" / "playbook.md",
        f"See ../{COMPANY_PLAYBOOK} for the live playbook.\n",
    )

    if register_project:
        try:
            from openagents_cli import projects_db as pdb

            conn = pdb.connect()
            try:
                pdb.create_project(
                    conn,
                    name=manifest["name"],
                    slug=slug,
                    folders=[str(root)],
                    primary_path=str(root),
                    description=manifest["mission"],
                    icon="🏢",
                )
            finally:
                conn.close()
        except Exception:
            logger.debug("projects_db registration skipped", exc_info=True)

    return root


def scaffold_company(
    root: Path,
    *,
    name: str,
    template: str = "startup",
    mission: str = "",
    register_project: bool = True,
    role_ids: Optional[List[str]] = None,
) -> Path:
    """Create company folder layout. Returns the company root path."""
    return apply_company_init(
        name=name,
        path=str(root),
        template=template,
        mission=mission,
        role_ids=role_ids,
        register_project=register_project,
    )


def _format_help() -> str:
    lines = [
        "Company workspace — multi-agent folder with roles, subagents, and skills.\n",
        "  /company init [name]            Guided setup (agent asks questions)",
        "  /company init <name> mission=…  Create directly (power user)",
        "  /company status                 Show active company from cwd",
        "  /company roles [role-id]        List roles or show details",
        "  /company delegate <role> <goal> Run work as a role (seeds next agent turn)",
        "",
        "Templates: startup (product team), studio (creative), minimal (ceo+worker)",
        "",
        "Examples:",
        "  /company init                   I'll ask about name, mission, roles, path",
        "  /company init OpenPro           Guided setup with name pre-filled",
        "  /company init Acme mission=\"Build SaaS\" template=startup path=./acme",
    ]
    root = find_company_root()
    if root:
        lines.insert(1, f"Active company: {root}\n")
    return "\n".join(lines)


def _format_status(root: Path) -> str:
    manifest = load_manifest(root)
    name = manifest.get("name") or root.name
    mission = manifest.get("mission") or ""
    roles = _list_role_ids(manifest)
    board = (manifest.get("kanban") or {}).get("board_slug") or "(not linked)"
    lines = [
        f"🏢 {name}",
        f"Path: {root}",
        f"Mission: {mission}",
        f"Template: {manifest.get('template', 'startup')}",
        f"Roles ({len(roles)}): {', '.join(roles) if roles else '(none)'}",
        f"Kanban board: {board}",
        "",
        "Next: `/company roles` · `/company delegate ceo <your goal>`",
    ]
    return "\n".join(lines)


def _format_roles(manifest: Dict[str, Any], role_id: Optional[str] = None) -> str:
    roles = manifest.get("roles") or []
    if role_id:
        entry = _role_from_manifest(manifest, role_id)
        if entry is None:
            known = _list_role_ids(manifest)
            close = difflib.get_close_matches(role_id, known, n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return f"Unknown role {role_id!r}.{hint}"
        skills = entry.get("skills") or []
        toolsets = entry.get("toolsets") or []
        return (
            f"**{entry.get('id')}** — {entry.get('title', '')}\n"
            f"delegate_role: {entry.get('delegate_role', 'leaf')}\n"
            f"toolsets: {', '.join(toolsets) if toolsets else '(default)'}\n"
            f"skills: {', '.join(skills) if skills else '(none)'}\n"
            f"focus: {entry.get('focus', '')}"
        )

    lines = ["Roles:\n"]
    for entry in roles:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("id", "?")
        title = entry.get("title", "")
        mode = entry.get("delegate_role", "leaf")
        lines.append(f"  • {rid} — {title} [{mode}]")
    lines.append("\n`/company roles <id>` for toolsets and skills.")
    return "\n".join(lines)


def _parse_kv(tokens: List[str]) -> Tuple[Dict[str, str], List[str]]:
    values: Dict[str, str] = {}
    rest: List[str] = []
    for tok in tokens:
        if "=" in tok:
            k, _, v = tok.partition("=")
            k = k.strip()
            if k:
                values[k] = v.strip()
                continue
        rest.append(tok)
    return values, rest


def _init_has_direct_params(kv: Dict[str, str]) -> bool:
    """True when the user supplied enough inline params to skip the interview."""
    return bool((kv.get("mission") or "").strip())


def _build_init_seed(
    *,
    name: Optional[str] = None,
    path: Optional[str] = None,
    template: Optional[str] = None,
    mission: Optional[str] = None,
    roles: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Agent instruction to interview the user, then create the company."""
    cwd = cwd or os.getcwd()
    template_catalog = "\n".join(
        f"  - **{key}**: {', '.join(r.role_id for r in roles_tuple)}"
        for key, roles_tuple in TEMPLATES.items()
    )
    known_name = (name or "").strip()
    known_path = (path or "").strip()
    known_template = (template or "").strip()
    known_mission = (mission or "").strip()
    known_roles = (roles or "").strip()

    prefill = []
    if known_name:
        prefill.append(f"- Company name (already given): {known_name}")
    if known_mission:
        prefill.append(f"- Mission (already given): {known_mission}")
    if known_template:
        prefill.append(f"- Template (already given): {known_template}")
    if known_path:
        prefill.append(f"- Folder path (already given): {known_path}")
    if known_roles:
        prefill.append(f"- Roles subset (already given): {known_roles}")
    prefill_block = "\n".join(prefill) if prefill else "- (nothing pre-filled yet)"

    default_path = known_path or (f"./{_slugify(known_name)}" if known_name else "./<slug>")

    return (
        "The user wants to create an OpenAgents **company workspace** — a folder with "
        "roles, subagent SOUL files, skills map, and a playbook for multi-agent work.\n\n"
        f"Working directory: `{cwd}`\n\n"
        "Already known:\n"
        f"{prefill_block}\n\n"
        "Interview the user for anything still missing. Ask **one question at a time**, "
        "offering sensible defaults in brackets:\n"
        "1. **Company name** — short display name\n"
        "2. **Mission** — 1–2 sentences on what this company builds or delivers\n"
        "3. **Team template** — one of:\n"
        f"{template_catalog}\n"
        "   Or pick a template and name which roles to keep (comma-separated ids).\n"
        "4. **Extra roles** (optional) — any specialist roles to add beyond the template? "
        "If yes, note their focus; you can add `roles/<id>.yaml` + `agents/<id>/` after "
        "scaffold by copying an existing role as a pattern.\n"
        f"5. **Folder path** — empty directory to create [default: {default_path}]\n"
        "6. **Confirm** — recap name, mission, template, roles, and path before creating.\n\n"
        "When you have final answers, create the company by running **one** terminal command "
        "(use the terminal tool). Quote paths/mission for the shell:\n"
        "```\n"
        "openagents company apply "
        "--name \"<Company Name>\" "
        "--path \"<folder>\" "
        "--template startup "
        "--mission \"<mission>\" "
        "[--roles ceo,engineer,researcher] "
        "[--no-project]\n"
        "```\n\n"
        "Rules:\n"
        "- Do not create files manually unless the user asked for custom roles beyond templates.\n"
        "- Refuse to scaffold into a non-empty folder; pick another path if needed.\n"
        "- After success, tell the user to `cd` into the folder and run "
        "`/company delegate ceo <first goal>`.\n"
        "- If they want kanban, suggest `/kanban init <slug>` next.\n"
    )


def _format_init_ack(name: Optional[str] = None) -> str:
    label = f" **{name}**" if name else ""
    return (
        f"Setting up company{label}… I'll ask a few questions on the next turn "
        "(name, mission, team template, roles, folder path)."
    )


def _build_delegate_seed(root: Path, role_id: str, goal: str) -> str:
    manifest = load_manifest(root)
    entry = _role_from_manifest(manifest, role_id)
    if entry is None:
        raise ValueError(f"unknown role {role_id!r}")

    soul_path = root / "agents" / role_id / "SOUL.md"
    soul_excerpt = ""
    if soul_path.is_file():
        soul_excerpt = soul_path.read_text(encoding="utf-8").strip()[:1200]

    toolsets = entry.get("toolsets") or []
    delegate_role = entry.get("delegate_role") or "leaf"
    skills = entry.get("skills") or []
    company_name = manifest.get("name") or root.name
    mission = manifest.get("mission") or ""

    toolsets_repr = repr(list(toolsets))
    return (
        f"You are operating as the **{entry.get('title', role_id)}** role for company "
        f"**{company_name}** (folder: `{root}`).\n\n"
        f"Company mission: {mission}\n\n"
        f"Role persona (from agents/{role_id}/SOUL.md):\n{soul_excerpt or '(see agents folder)'}\n\n"
        f"Goal: {goal}\n\n"
        f"Instructions:\n"
        f"- Work inside `{root}`; deliverables go in `workspace/`.\n"
        f"- Read `{COMPANY_PLAYBOOK}` if you need workflow context.\n"
        + (
            f"- Recommended skills to load if available: {', '.join(skills)}.\n"
            if skills
            else ""
        )
        + (
            f"- You are the orchestrator: break work into parallel `delegate_task` calls "
            f"with role='leaf' for specialists. Use toolsets from each role in "
            f"`roles/*.yaml`. Max concurrent: "
            f"{(manifest.get('delegation') or {}).get('max_concurrent_children', 3)}.\n"
            if delegate_role == "orchestrator"
            else (
                f"- Execute this yourself using tools; toolsets for this role: "
                f"{', '.join(toolsets) if toolsets else 'inherit'}.\n"
                f"- If you need help, ask the user to `/company delegate ceo ...`.\n"
            )
        )
        + (
            f"- When delegating as this role, pass toolsets={toolsets_repr} and "
            f"role='{delegate_role}' to delegate_task.\n"
            if delegate_role == "orchestrator"
            else ""
        )
    )


def handle_company_command(args: str) -> CompanyCommandResult:
    """Dispatch ``/company`` — args are everything after the command name."""
    raw = (args or "").strip()
    if not raw:
        root = find_company_root()
        text = _format_help()
        if root:
            text = _format_status(root) + "\n\n" + text
        return CompanyCommandResult(text=text)

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        return CompanyCommandResult(text=f"Could not parse arguments: {exc}")

    if not tokens:
        return CompanyCommandResult(text=_format_help())

    verb = tokens[0].lower()
    rest = tokens[1:]

    if verb == "init":
        kv, leftovers = _parse_kv(rest)
        name = leftovers[0] if leftovers else kv.get("name", "").strip() or None
        path_arg = leftovers[1] if len(leftovers) > 1 else kv.get("path", "").strip() or None
        template = kv.get("template", "").strip() or None
        mission = kv.get("mission", "").strip()
        roles_raw = kv.get("roles", "").strip()
        register = kv.get("register_project", "true").lower() not in {"0", "false", "no"}

        if not _init_has_direct_params(kv):
            return CompanyCommandResult(
                text=_format_init_ack(name),
                agent_seed=_build_init_seed(
                    name=name,
                    path=path_arg,
                    template=template,
                    mission=mission or None,
                    roles=roles_raw or None,
                    cwd=os.getcwd(),
                ),
            )

        if not name:
            return CompanyCommandResult(
                text="Company name required for direct init. "
                "Use `/company init <name> mission=\"…\"` or bare `/company init` for guided setup."
            )

        path_final = path_arg or f"./{_slugify(name)}"
        template_final = template or "startup"
        role_ids = _parse_role_ids(roles_raw)

        try:
            root = apply_company_init(
                name=name,
                path=path_final,
                template=template_final,
                mission=mission,
                role_ids=role_ids,
                register_project=register,
            )
        except FileExistsError as exc:
            return CompanyCommandResult(text=str(exc))
        except ValueError as exc:
            return CompanyCommandResult(text=str(exc))
        except Exception as exc:
            logger.exception("company init failed")
            return CompanyCommandResult(text=f"Company init failed: {exc}")

        manifest = load_manifest(root)
        roles_list = _list_role_ids(manifest)
        return CompanyCommandResult(
            text=(
                f"Created company **{manifest.get('name')}** at `{root}`\n"
                f"Roles: {', '.join(roles_list)}\n"
                f"Read `{COMPANY_PLAYBOOK}` · try `/company delegate ceo <goal>`"
            )
        )

    if verb in {"status", "show"}:
        root = find_company_root()
        if root is None:
            return CompanyCommandResult(text="No company.yaml found in cwd or parent directories.")
        return CompanyCommandResult(text=_format_status(root))

    if verb == "roles":
        root = find_company_root()
        if root is None:
            return CompanyCommandResult(text="No company.yaml found. Run `/company init <name>` first.")
        manifest = load_manifest(root)
        role_id = rest[0] if rest else None
        return CompanyCommandResult(text=_format_roles(manifest, role_id))

    if verb in {"delegate", "spawn", "run"}:
        if len(rest) < 2:
            return CompanyCommandResult(
                text="Usage: /company delegate <role-id> <goal...>"
            )
        root = find_company_root()
        if root is None:
            return CompanyCommandResult(
                text="No company.yaml found. Run `/company init <name>` in an empty folder first."
            )
        role_id = rest[0].lower()
        goal = " ".join(rest[1:])
        try:
            seed = _build_delegate_seed(root, role_id, goal)
        except ValueError as exc:
            return CompanyCommandResult(text=str(exc))
        title = _role_from_manifest(load_manifest(root), role_id) or {}
        label = title.get("title") or role_id
        return CompanyCommandResult(
            text=f"Spawning **{label}** for: {goal[:120]}{'…' if len(goal) > 120 else ''}",
            agent_seed=seed,
        )

    close = difflib.get_close_matches(verb, ["init", "status", "roles", "delegate", "spawn"], n=1)
    hint = f" Did you mean `{close[0]}`?" if close else ""
    return CompanyCommandResult(text=f"Unknown subcommand `{verb}`.{hint}\n\n" + _format_help())


# ---------------------------------------------------------------------------
# Terminal CLI — ``openagents company apply …`` (used by guided init)
# ---------------------------------------------------------------------------


def build_parser(parent_subparsers) -> Any:
    """Attach the ``company`` subcommand tree."""
    import argparse

    parser = parent_subparsers.add_parser(
        "company",
        help="Create and manage multi-agent company workspaces",
        description=(
            "Scaffold company folders with roles, subagent configs, and skills maps. "
            "The interactive `/company init` flow uses `company apply` after interviewing "
            "the user."
        ),
    )
    sub = parser.add_subparsers(dest="company_action")

    apply_p = sub.add_parser(
        "apply",
        help="Create a company workspace from explicit parameters",
    )
    apply_p.add_argument("--name", required=True, help="Company display name")
    apply_p.add_argument(
        "--path", required=True, help="Empty folder to create (relative or absolute)",
    )
    apply_p.add_argument(
        "--template",
        default="startup",
        choices=sorted(TEMPLATES.keys()),
        help="Role template preset",
    )
    apply_p.add_argument("--mission", default="", help="Company mission statement")
    apply_p.add_argument(
        "--roles",
        default="",
        help="Comma-separated role ids to include from the template (ceo always kept)",
    )
    apply_p.add_argument(
        "--no-project",
        action="store_true",
        help="Skip projects.db registration",
    )

    parser.set_defaults(_company_parser=parser)
    return parser


def company_command(args) -> int:
    """Entry point from ``openagents company …`` argparse dispatch."""
    import sys

    action = getattr(args, "company_action", None)
    if action != "apply":
        parser = getattr(args, "_company_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: openagents company apply --name … --path …", file=sys.stderr)
        return 1

    role_ids = _parse_role_ids(getattr(args, "roles", "") or "")
    try:
        root = apply_company_init(
            name=args.name,
            path=args.path,
            template=args.template,
            mission=args.mission or "",
            role_ids=role_ids,
            register_project=not getattr(args, "no_project", False),
        )
    except (FileExistsError, ValueError) as exc:
        print(f"company apply failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        logger.exception("company apply failed")
        print(f"company apply failed: {exc}", file=sys.stderr)
        return 1

    manifest = load_manifest(root)
    roles = _list_role_ids(manifest)
    print(f"Created company {manifest.get('name')} at {root}")
    print(f"Roles: {', '.join(roles)}")
    return 0

