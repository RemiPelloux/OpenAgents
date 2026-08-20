---
name: open-pro
description: "Flutter OpenPro-Mobile hiring app development."
version: 1.1.0
author: OpenPro
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openpro, flutter, mobile, hiring]
    category: open-ecosystem
    related_skills: [open-ecosystem-hub, open-app, openpro-tiktok-prospection]
---

# Open Pro (Flutter mobile)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

**OpenPro-Mobile** — candidate + recruiter hiring app. Outside OpenOS umbrella; integrates via APIs.

## When to Use

- Screens, providers, repos in `OpenPro-Mobile/`
- API, auth, Sendbird chat, i18n EN/FR
- Build APK/iOS

Not for OpenAgents agent logic — use `openagents` skill.

## Structural overview

```
lib/mvc/screens/{common,candidate,recruiter}/
lib/data/repositories/     # API
lib/core/services/         # DI
lib/store/                 # MobX global state
```

Import via `lib/config/barrier.dart`.

## Prerequisites

- Flutter SDK matching project `pubspec`
- Firebase/auth configured per env docs
- `openpro-mobile` Cursor skill for deep Flutter patterns

## Procedure

1. Identify screen layer (candidate vs recruiter)
2. Repository → service → controller → screen
3. `Either<Failure, T>` for API errors — no silent catch
4. Run `flutter test` + analyzer before handoff

## Decision rules

| Change | Layer |
|--------|-------|
| API shape | repository + `api_constants.dart` |
| UI state | ChangeNotifier controller |
| Global auth/theme | MobX store + `build_runner` |

## Pitfalls

- Bypassing `ApiClient` token injection
- Hardcoded strings — use `app_en.arb` / `app_fr.arb`
- Agent automation inside Flutter UI (belongs in OpenAgents/OpenPro backend)

## Verification

- [ ] `flutter analyze` clean on touched files
- [ ] Widget test for changed screen if logic non-trivial
- [ ] EN + FR keys added in pairs
