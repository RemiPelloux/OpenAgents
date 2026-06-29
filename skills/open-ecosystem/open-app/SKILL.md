---
name: open-app
description: "Use when launching or developing Open client apps — OpenAgents desktop, web dashboard, TUI, and mobile shells (Open Pro)."
version: 1.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [desktop, electron, tui, dashboard, web, gui, openagents, mobile]
    related_skills: [open-ecosystem-hub, openagents, open-pro]
---

# Open App

**Open App** covers all **client surfaces** for the Open suite — where users interact with agents and products outside of raw terminal scripts.

## Surfaces map

| Surface | Command / path | Best for |
|---------|----------------|----------|
| **CLI** | `openagents` | Power users, scripting, SSH |
| **TUI** | `display.interface: tui` in config | Terminal-native interactive UI (`ui-tui/`) |
| **Web dashboard** | `openagents dashboard` | Admin, config, skills, MCP, profiles (`web/`) |
| **Desktop app** | `openagents desktop` / `openagents gui` | Daily driver (`apps/desktop/`) |
| **Gateway bots** | `openagents gateway` | Telegram, Discord, Slack, WhatsApp, … |
| **Mobile** | Open Pro Flutter app | Hiring (`open-pro` skill) |
| **ACP (IDE)** | `openagents acp` | VS Code / Zed / JetBrains |

## When to use

- User says "open the app", "desktop", "dashboard", or "GUI"
- Building or debugging Electron desktop, React web, or Ink TUI
- Choosing which surface fits a workflow
- Packaging, auto-update, or desktop gateway integration

## Launch commands

```bash
openagents                    # CLI chat (default)
openagents desktop            # Electron desktop (alias: gui)
openagents dashboard          # Local web admin + chat
openagents gateway start      # Messaging platforms (background service)
openagents acp                # Editor integration
```

TUI mode — set in `~/.openagents/config.yaml`:

```yaml
display:
  interface: tui    # instead of classic CLI
```

Then run `openagents` to launch the Ink TUI (`ui-tui/` package).

## Desktop app (`apps/desktop/`)

- Electron shell wrapping agent + gateway lifecycle
- Model/provider settings UI, project context, update button
- Uses embedded gateway; close other `openagents` processes before `openagents update` on Windows

Dev (from repo root):

```bash
cd apps/desktop
npm install
npm run dev
```

## Web dashboard (`web/`)

- React admin: profiles, skills, MCP catalog, cron, logs
- Served by `openagents dashboard` — default bind localhost
- Build: `cd web && npm run build` (bundled into package on release)

## TUI (`ui-tui/`)

- TypeScript + Ink terminal UI
- Shared agent core with CLI; different input/rendering layer
- Tests: `cd ui-tui && npm test`

## Mobile (Open Pro)

Not built from this repo — see **`open-pro`** skill for `OpenPro-Mobile/` Flutter app.

## Surface selection guide

| Need | Surface |
|------|---------|
| Quick terminal task | CLI |
| Rich terminal UX | TUI |
| Visual config + skills | Web dashboard |
| Always-on daily driver | Desktop |
| Phone hiring workflows | Open Pro mobile |
| Chat from Telegram | Gateway |
| Code in IDE | ACP |

## Updates per surface

```bash
openagents update             # Pull OpenAgents + refresh deps
# Desktop: restart app after update; may auto-trigger update from UI
# Web: rebuild if developing locally (npm run build)
# Mobile: separate Flutter release pipeline (open-pro)
```

## Common pitfalls

1. **Multiple gateways** — one bot token per profile; use token locks across profiles
2. **Windows update lock** — desktop backend holds `openagents.exe`; stop before update
3. **Dashboard auth** — enable dashboard auth in production exposes; don't bind `0.0.0.0` without auth
4. **Wrong surface for task** — long file edits better in CLI/TUI/ACP than mobile

## Verification checklist

- [ ] Correct surface chosen for user workflow
- [ ] `openagents doctor` passes for desktop/gateway paths
- [ ] Dashboard reachable and authenticated if exposed beyond localhost
- [ ] Desktop update completes without exe lock errors
- [ ] Mobile tasks delegated to `open-pro` skill
