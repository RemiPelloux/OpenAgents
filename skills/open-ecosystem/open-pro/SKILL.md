---
name: open-pro
description: "Use when developing Open Pro (OpenPro-Mobile) — Flutter hiring app for candidates and recruiters."
version: 1.0.0
author: Remi Pelloux
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [openpro, flutter, mobile, hiring, recruiter, candidate, dart]
    related_skills: [open-ecosystem-hub, openagents, open-app]
---

# Open Pro

**Open Pro** is the Flutter mobile application for professional hiring — dual roles (candidate and recruiter), chat (Sendbird), API-driven workflows, and localized EN/FR UI.

## When to use

- Adding or fixing screens in `OpenPro-Mobile/`
- Providers, repositories, services, navigation, or i18n
- API integration, auth, or Sendbird chat
- Build, test, or release (APK/iOS)

Do **not** use for OpenAgents agent logic — see `openagents`.

## Project layout

```
OpenPro-Mobile/
├── lib/
│   ├── main.dart              # Entry + MultiProvider
│   ├── config/barrier.dart    # Barrel import (use this everywhere)
│   ├── mvc/screens/{common,candidate,recruiter}/
│   ├── mvc/controllers/       # ChangeNotifier per screen
│   ├── data/repositories/     # API repositories
│   ├── core/services/         # DI services
│   ├── core/constants/        # api_constants.dart
│   └── config/l10n/           # app_en.arb, app_fr.arb
└── docs/                      # architecture, features, api, testing
```

## Architecture rules

| Layer | Technology | Scope |
|-------|------------|--------|
| Global state | MobX (`lib/store/`) | Auth, theme, language — run `build_runner` after edits |
| Screen state | Provider / ChangeNotifier | Loading, lists, forms per screen |
| HTTP | `ApiClient` | Auto-injects Firebase token |
| Errors | `Either<Failure, T>` | Always fold; no silent catches |
| DI | `service_locator.dart` | Register lazy singletons |

**Never** mix MobX for screen state or Provider for global state.

## Add a new screen

1. Create `lib/mvc/screens/<role>/my_screen.dart` — import `package:open_pro/config/barrier.dart`
2. Create matching provider in `lib/mvc/controllers/<role>/`
3. Register `ChangeNotifierProvider` in `main.dart`
4. Export from `barrier.dart`
5. Add keys to `app_en.arb` and `app_fr.arb`

## Add an API endpoint

1. Constant in `lib/core/constants/api_constants.dart`
2. Method on repository (`lib/data/repositories/user_api_repositories.dart` or domain repo)
3. Call from provider with `result.fold((failure) => ..., (data) => ...)`

## Commands

```bash
cd OpenPro-Mobile
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter run
flutter test
flutter analyze
flutter build apk --release
flutter build ios --release
```

## Deep docs

Read when implementing non-trivial features:

- `OpenPro-Mobile/docs/architecture.md` — layers, DI, data flow
- `OpenPro-Mobile/docs/features.md` — screens and user flows
- `OpenPro-Mobile/docs/api-services.md` — endpoints and integrations
- `OpenPro-Mobile/docs/testing.md` — mocks and coverage

## Common pitfalls

1. **Magic strings** — use `KApi.*` and l10n keys, not hardcoded labels
2. **Missing build_runner** — MobX store changes require codegen
3. **Wrong role folder** — candidate vs recruiter paths are strict
4. **Bypassing ApiClient** — breaks auth and error handling

## Verification checklist

- [ ] Imports use `barrier.dart`
- [ ] New strings in both EN and FR ARB files
- [ ] Provider registered in `main.dart`
- [ ] `flutter analyze` clean
- [ ] Tests updated for new repository/provider behavior
