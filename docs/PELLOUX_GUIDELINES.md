# Pelloux guidelines — OpenAgents fork

Standards for this fork ([RemiPelloux/OpenAgents](https://github.com/RemiPelloux/OpenAgents)). Apply to new features, docs, and install UX.

## Product

1. **Brand:** OpenAgents (not Hermes) in user-facing text; `hermes` CLI alias is deprecated only.
2. **Theme:** Default skin **OpenCode** — warm dark terminal, blue accent (`display.skin: opencode`).
3. **Distribution:** Users pull **`origin`** (this repo); Hermes upstream is maintainer-only (`scripts/sync_from_hermes.sh`).

## Developer experience

1. **No `source venv`:** After `./scripts/install-local.sh`, run `openagents` from anywhere via `~/.local/bin`.
2. **Fast reinstall:** Re-run install script to refresh deps; it **reuses** the venv unless `--recreate`.
3. **Link-only:** `./scripts/install-local.sh --link-only` after `git pull` if links break.
4. **Bytecode cache:** Launcher sets `PYTHONPYCACHEPREFIX=~/.openagents/cache/pycache` for faster repeat CLI starts.

## Architecture (from Technical Lead standards)

1. **Single responsibility** — one module, one job; keep files ≤300 lines when practical.
2. **Minimal public API** — extend existing patterns (`openagents_fork.py`, `skin_engine`, `config.py`).
3. **No drive-by refactors** — fork changes stay scoped (rebrand, install, docs, Pelloux UX).
4. **Tests for fork logic** — `tests/test_openagents_fork.py`, skin tests, update checks.

## Performance

1. **CLI startup** — defer heavy imports (already upstream); avoid new module-level side effects.
2. **MCP** — discovery runs in background; tune `mcp_discovery_timeout` in config if needed (default 1.5s).
3. **Updates** — `updates.pre_update_backup: false` for daily dev (opt in for production gateways).
4. **Install** — default `.[all]` without `dev` unless `--dev`; use `uv` when available.

## Provider defaults

1. **Codex:** Document `openagents auth add openai-codex` + `openagents model` in README.
2. **Secrets:** `~/.openagents/.env` only; never commit keys.
3. **Profiles:** Use `OPENAGENTS_HOME` / `-p` for isolation.

## Git & upstream

1. **Commit messages** — complete sentences; explain *why*.
2. **Upstream sync** — `./scripts/sync_from_hermes.sh`, then rebrand script, then tests.
3. **License** — keep MIT notices for Nous Research + Remi Pelloux.

## Checklist (PR / release)

- [ ] User-facing strings say OpenAgents
- [ ] `openagents doctor` passes on clean install
- [ ] `./scripts/install-local.sh` tested (fresh + reuse venv)
- [ ] README + `docs/UPSTREAM.md` updated if workflow changed
- [ ] No hardcoded `~/.Hermes` paths (use `get_openagents_home()`)
