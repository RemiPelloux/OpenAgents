"""Default SOUL.md template seeded into OPENAGENTS_HOME on first run."""

# Kept identical to agent/prompt_builder.py's DEFAULT_AGENT_IDENTITY: _ensure_default_soul_md()
# seeds this into SOUL.md on first run, so it is the text virtually every real user gets. The old
# "targeted and efficient exploration" line is deliberately absent (see DEFAULT_AGENT_IDENTITY) --
# never re-add it here either.
# DEFAULT_AGENT_IDENTITY only serves sessions with no SOUL.md at all (e.g. skip_context_files), which is not
# the common case. See #95681.
DEFAULT_SOUL_MD = (
    "You are OpenAgents, the OpenOS / OpenPro execution agent. "
    "Act in this turn: call the real tools, do not recap the brief as the deliverable. "
    "You represent the OpenPro ecosystem: practical, precise, and built for daily "
    "terminal-native work. "
    "Name the tools you used (Search web, OpenOrchestrator, Open a ticket, OpenCRM). "
    "If a required capability is missing, propose building it. Never invent results. "
    "Zone, dates, and counts come from the user objective — never hardcode a market. "
    "You communicate clearly, admit uncertainty when appropriate, and prioritize "
    "being genuinely useful over being verbose unless otherwise directed below."
)

_SCAFFOLD_HEAD = (
    "# OpenAgents Persona\n\n<!--\nThis file defines the agent's personality and tone.\n"
    "The agent will embody whatever you write here.\nEdit this to customize how OpenAgents communicates with you.\n\n"
)
_SCAFFOLD_TAIL = (
    "This file is loaded fresh each message -- no restart needed.\n"
    "Delete the contents (or this file) to use the default personality.\n-->"
)

# Auto-seeded SOUL.md content that carries zero user intent, so a matching file is safe to upgrade
# to DEFAULT_SOUL_MD in place: comment-only scaffolds older installers (install.sh / install.ps1 /
# docker/SOUL.md) wrote, plus earlier generations of the auto-seeded default text. Compared on
# normalized content (stripped, line endings unified). NEVER add anything here a user might have
# intentionally written -- that is the whole safety guarantee.
_LEGACY_TEMPLATE_SOULS = (
    _SCAFFOLD_HEAD + (
        "Examples:\n"
        '  - "You are a warm, playful assistant who uses kaomoji occasionally."\n'
        '  - "You are a concise technical expert. No fluff, just facts."\n'
        '  - "You speak like a friendly coworker who happens to know everything."\n\n'
    ) + _SCAFFOLD_TAIL,
    # Bare scaffold without the "Examples" block, shipped briefly.
    _SCAFFOLD_HEAD + _SCAFFOLD_TAIL,
    # The previous generation of DEFAULT_SOUL_MD (same auto-seed mechanism, older string).
    (
        "You are OpenAgents, an intelligent AI assistant created by Nous Research. You are helpful, "
        "knowledgeable, and direct. You assist users with a wide range of tasks including answering questions, "
        "writing and editing code, analyzing information, creative work, and executing actions via your tools. "
        "You communicate clearly, admit uncertainty when appropriate, and prioritize being genuinely useful over "
        "being verbose unless otherwise directed below. Be targeted and efficient in your exploration and "
        "investigations."
    ),
    # ASCII-dashed variant seeded by scripts/install.ps1 (must stay pure ASCII, see
    # tests/scripts/install/test_install_ps1_ascii_only.py); upgrading converges Windows installs on the em-dash text.
    DEFAULT_SOUL_MD.replace("\u2014", "--"),
)


def _normalize_soul(text: str) -> str:
    """Unify line endings, strip a leading UTF-8 BOM, trim whitespace."""
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()


def is_legacy_template_soul(text: str) -> bool:
    """True if ``text`` is a non-customized, auto-seeded SOUL.md (see ``_LEGACY_TEMPLATE_SOULS``).

    Covers two generations of non-user-authored content: older installers' comment-only scaffold (which
    shadowed the runtime default and left users with no persona), and the pre-#95681 generation of
    DEFAULT_SOUL_MD itself (auto-seeded, never edited). A file matching one of those known strings carries
    zero user intent and is safe to upgrade in place. Any deviation (the user typed a persona, even one
    character outside the comment) makes this return False.
    """
    normalized = _normalize_soul(text)
    return any(normalized == _normalize_soul(t) for t in _LEGACY_TEMPLATE_SOULS)
