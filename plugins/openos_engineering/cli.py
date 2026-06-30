"""CLI: openagents openos init-profiles"""

from __future__ import annotations

from plugins.openos_engineering.profiles import init_profiles


def openos_init_profiles_command(_args) -> str:
    names = init_profiles()
    return "Created OpenOS profiles: " + ", ".join(names)
