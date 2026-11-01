# frontend

Everything that runs in a browser or on a phone, in JavaScript (`.js`/`.jsx` with JSDoc hints). Owner: Yash Salunkhe.

| Folder | What |
|---|---|
| `mobile/` | Vite + React 19 app → PWA (Vercel) and Capacitor 8 (Android). One app for the field roles: customer, captain, kitchen, rider, packer |
| `console/` | Next.js 16 web console for admin, hub manager, franchise owner and NGO, plus public pages |
| `packages/` | Code shared by both apps: `ui/` (design system), `api-client/` (generated), `domain/` |

Inside an app, code is grouped by role (routes) and by backend module (`features/identity`, `features/orders`, …).
The apps talk to the backend only through the generated `packages/api-client`.

See `docs/handbook/09-frontend-and-design.md`, `docs/FRONTEND_DESIGN.md` and `docs/design/README.md`.

## The Android app (`mobile/android`)

Capacitor 8 wraps the same `mobile/` build (`vite build --mode native`: no service worker, Bearer tokens, the
API at `VITE_API_BASE_URL`). Plugins are listed in ADR-0031; app code reaches them only through
`mobile/src/shared/native/` (`withPlugin`), so the web bundle never loads them.

Once per machine: JDK 21 as `JAVA_HOME` and as Android Studio's Gradle JDK, SDK Platform 36, an emulator from a
Google Play image (`docs/SETUP.md` §1). Put `mobile/android/app/google-services.json` in place (from the Firebase
console or Infisical; never committed). `mobile/.env.local` needs `VITE_API_BASE_URL` (staging:
`https://gharbasket-api.onrender.com`) and the `VITE_FIREBASE_*` values from `pnpm env:pull`.

| Command (in `mobile/`) | What |
|---|---|
| `pnpm cap:sync` | native web build, then copy it and the plugins into `android/` |
| `pnpm android:run` | sync, build and start on the running emulator or the USB phone |
| `pnpm android:apk` | sync and build a debug APK: `android/app/build/outputs/apk/debug/app-debug.apk` (`adb install -r …`) |
| `pnpm android:open` | open `android/` in Android Studio |

`android/` is committed except build output, the copied web assets and generated config (its `.gitignore`).
Staging has no dev sign-in: on a phone, sign in with a real number or a Firebase test number.
