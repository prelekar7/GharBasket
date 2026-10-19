# Kickoff for Yash Salunkhe — UI design now, frontend next

Owner: Yash Salunkhe (Track A: mobile app + design system; Track B: commerce backend M1–M5)
Prepared: 2 October 2026 · Works in parallel with Prathamesh Relekar (infra + backend platform)

## 1. Get the repo and start Claude Code
```bash
git clone https://github.com/prelekar7/GharBasket
cd GharBasket
git checkout docs/handbook      # use main once the docs PR is merged
claude                          # start Claude Code inside the repo, then paste the prompt in section 5
```
Fallback without a clone: on claude.ai, attach the Phase A files from section 3 and paste the same prompt.

## 2. Shared Claude account — rules
- Artifacts are visible to both of us in the gallery (`/artifacts` in Claude Code, or claude.ai/code/artifacts). The project handbook is at https://claude.ai/artifact/QZK3YiuUeUknK8V7kDyT7j (read only).
- Claude's memory is per machine, so your Claude knows nothing about the planning discussion. The repo docs are its only context.
- Usage limits are shared between our two sessions. Avoid running very heavy tasks at the same time.
- Yash owns the artifacts named `GharBasket · Design System`, `GharBasket · Customer screens`, `GharBasket · Field-role screens` and `GharBasket · Console screens`. Prathamesh's sessions never create or edit design artifacts.

## 3. Files your Claude reads
| Phase A — design (now) | Why |
|---|---|
| `docs/FRONTEND_DESIGN.md` | Full design spec: principles, brand, tokens, type, icons, motion, components, screens |
| `docs/handbook/09-frontend-and-design.md` | Screen flows per role, design workflow |
| `docs/handbook/01-vision-and-scope.md` | USPs, personas, demo-day story |
| `docs/handbook/03-product-and-features.md` | Roles, features, use cases |
| `docs/handbook/07-workflows.md` (W1, W2, W3) | Onboarding, weekly cycle, payments |
| `docs/modules/M1-identity.md`, `M3-subscriptions.md`, `M5-billing.md`, `M9-community.md` | Screens and acceptance criteria |
| `docs/adr/0002`, `0012`, `0016`, `0017` | Frontend stack, Android + PWA, Claude Design only, design before code |

| Phase B — code (after `chore/M0-scaffold` is merged) | Why |
|---|---|
| `AGENTS.md`, `docs/handbook/04-system-architecture.md`, `10-api-and-integration.md`, `11-engineering-process.md` | Boundaries, API conventions, git and CI rules |
| `docs/modules/M0-platform.md` and section 3.1 of `docs/modules/M1-identity.md` | What the shells and the login screen must call |

## 4. Work list — Phase A (design)
1. **`GharBasket · Design System`** artifact: tokens, type scale, role accents, icon style, base and signature components, light and dark. Done when Prathamesh, Rhythm and Manish have commented and the revisions are made.
2. **`GharBasket · Customer screens`** canvas, in this order:
   1. Onboarding: language, OTP, society picker, household stepper, consent, payment mode.
   2. Week (Thali calendar).
   3. Box (Parchi) and the Substitute sheet.
   4. Wallet and top-up.
   5. Society unlock.
   6. Orders and tracking, Kindness, Profile with senior mode, Become a partner, Assistant.

   Wireframe artboards first, then high fidelity. English and Hindi copy. Phone size 390 × 844.
3. Bilingual "घरBasket" wordmark, the first 15 custom icons, and 3 empty-state illustrations.
4. Post all artifact links in a GitHub issue titled "Design: Customer v1". Later, list them in `docs/design/README.md` in your first code PR.
5. Hand-off to code: the token values become `packages/ui/tokens.css` in your `feat/M0-ui-tokens` PR.

## 5. Prompt to paste into Claude Code
```
You are helping Yash Salunkhe, owner of Track A (mobile app + design system) and Track B (commerce backend M1–M5)
of GharBasket, a 4-person B.Tech project. Prathamesh Relekar is building infra + backend platform in parallel,
in another Claude session on the SAME Claude account. You have no memory of earlier planning: the repo docs are the
source of truth. Read them before doing anything.
Shared-account rules: only create/edit artifacts named "GharBasket · Design System", "GharBasket · Customer screens",
"GharBasket · Field-role screens", "GharBasket · Console screens". Never edit "GharBasket Handbook"
(https://claude.ai/artifact/QZK3YiuUeUknK8V7kDyT7j) — open it only to read.
Start by summarising, in 10 lines, what you understood from the docs, and the Phase A deliverables in priority order.
Then wait for Yash to confirm before creating any artifact.

READ FIRST: docs/handoff/yash-kickoff-prompt.md, docs/handbook/00-README.md, 01-vision-and-scope.md,
03-product-and-features.md, 07-workflows.md, 09-frontend-and-design.md, docs/FRONTEND_DESIGN.md,
docs/adr/0002, 0012, 0016, 0017, docs/modules/M1-identity.md … M5-billing.md, M9-community.md, AGENTS.md.
Decisions in docs/adr are final; propose changes only via a new ADR.

PHASE A — DESIGN NOW (no repo commits yet), using Claude Design:
1. "GharBasket · Design System" artifact (from the Design System type):
   tokens kraft-50 #FBF7F0, kraft-100 #F3EBDD, ink-900 #1E1B16 / 700 #4A443B / 500 #7A7266, turmeric-500 #E8A33D,
   leaf-700 #2F5D3A, chilli-600 #C8442B, neel-600 #2E4A7D, slate-500 #5E6B73; dark mode warm charcoal #1A1714;
   role accents customer=leaf, captain=turmeric, kitchen=chilli, rider=neel, packer=slate;
   type Fraunces (display, SOFT axis) + Mukta (UI, Latin + Devanagari), scale 12/14/16/18/22/28/36, senior mode +2 steps;
   icons Phosphor (regular for UI, duotone for category tiles) + a custom 1.5px-stroke food/household set;
   components: base set (Button, Sheet, QtyStepper, Chip, StatusPill, Toast, Skeleton, EmptyState…) and signature set
   (ParchiList/ParchiLine with reason chips, ThaliWeek, SubstituteSheet, PriceSwapChip, MoneyText, WalletCard,
   GullakMeter, SocietyUnlockBar, KnowYourCookCard, RoleSwitcher).
   Banned: purple/blue gradients, glassmorphism, emoji-as-icons, default shadcn/Material look, stock 3D art.
2. "GharBasket · Customer screens" Design canvas attached to the design system: wireframes then hi-fi for
   onboarding (language, OTP, society picker, household stepper, consent, payment mode), Week/Thali, Box/Parchi,
   Substitute sheet, Wallet + top-up, Society unlock. Phone 390×844; English + Hindi copy.
3. Post the artifact links in GitHub issue "Design: Customer v1"; iterate on team comments.

PHASE B — CODE (only after Prathamesh's "chore/M0-scaffold" PR is merged to main), one You are helping Yash Salunkhe on GharBasket (4-person B.Tech project, SIES GST, guide Prof. Kranti Bade).
Yash owns ALL FRONTEND + DESIGN: apps/mobile, apps/console, packages/ui, packages/domain.
Prathamesh Relekar owns ALL BACKEND + INFRA (services/, infra/, data/, scripts/, .github/, root configs) and works
in parallel in another Claude session on the SAME Claude account. You have no memory of earlier planning:
the repo docs on main are the source of truth (docs/handbook/, docs/FRONTEND_DESIGN.md, docs/modules/, docs/adr/, AGENTS.md).
Some docs still mention TypeScript, AWS or Hey API; the decisions in THIS prompt override them (Prathamesh's next docs PR updates them).

=== PART 1 — SYNC NOW (do this first, then stop and show me the PR link) ===
1. Run `git status` and `git remote -v`. If this folder is not a clone of github.com/prelekar7/GharBasket, clone it
   next to this folder and copy my work into apps/mobile, apps/console, packages/ui, packages/domain.
2. Create branch `feat/frontend-phase-b-initial`; commit all my current work
   ("feat(frontend): initial Phase B import").
3. `git fetch origin && git rebase origin/main`. On conflicts in root files (package.json, pnpm-workspace.yaml,
   turbo.json, biome.json, justfile, .gitignore, AGENTS.md and its copies) keep main's version and re-add only lines my
   apps truly need, and list every root-file change in the PR description. Never touch services/, infra/, data/,
   scripts/, .github/.
4. `pnpm install` (regenerates pnpm-lock.yaml, installs git hooks), then `pnpm exec biome check .` and fix issues.
5. Nothing secret may be committed: no .env*, google-services.json, *.jks/*.keystore, keys or tokens.
   If gitleaks is installed: `gitleaks git --log-opts="origin/main..HEAD"`.
6. Make sure there are NO .ts/.tsx files (JavaScript only; convert any you find).
7. Push and open a DRAFT PR to main: "feat(frontend): initial Phase B import". Description: what is done, what is not,
   screenshots, libraries + versions used, root-file changes.
8. Post the "GharBasket · Design System" and "GharBasket · Customer screens" artifact links as a comment on issue #3.
Do not merge, do not push to main. Prathamesh's Claude reviews the PR; fix its comments, then Prathamesh merges.
After the merge: `git checkout main && git pull origin main`, and all further work is small PRs from main.

=== PART 2 — RULES ===
- JavaScript only: .js/.jsx. Add jsconfig.json (checkJs: true, path aliases) for editor hints; JSDoc on shared
  components and hooks. Hints never block a build.
- Formatting/lint: Biome (repo config). Commits: Conventional Commits with scope, e.g. "feat(mobile): role chooser".
- Small PRs (about 400 changed lines max) from branches off main; `git pull --rebase origin main` daily.
- Secrets live in Infisical (ask Prathamesh for an invite). Frontend gets PUBLIC values only, as VITE_* / NEXT_PUBLIC_*
  variables (Firebase web config, Razorpay key id, PostHog key/host). Never paste secrets into chat or issues.
- Staging holds synthetic data only. Never send phone numbers or names to PostHog (use user ids).
- Never hand-edit packages/api-client: it is generated by `just gen-api` (openapi-fetch + openapi-react-query,
  plus a generated schema.d.ts used only for editor autocomplete). Prathamesh publishes it in an early backend PR.
- Verify every library's current API with Context7 before coding. Ask Yash before any decision the docs don't cover.

=== PART 3 — AUTH CONTRACT AND SESSIONS (frozen; see docs/modules/M1-identity.md section 3.1) ===
- Login: Firebase phone auth on the device (staging uses Firebase fictional TEST NUMBERS only; on web set
  appVerificationDisabledForTesting for test numbers) -> POST /v1/auth/exchange { firebase_id_token, device:{platform,
  push_token} } -> { access_token (15 min JWT), access_expires_in, refresh_token, is_new_user, user }.
  GET /v1/me -> user + roles[]. Errors are application/problem+json with a `code` (e.g. auth.invalid_token).
  Dev login (dev_phone) exists ONLY on a local backend, never on staging.
- Native app (Capacitor): keep the access token in memory and the refresh token in OS secure storage (pick the plugin in
  spike S-2); send Authorization: Bearer; keep CapacitorHttp disabled.
- Web (browser PWA + console): the API sets httpOnly cookies, so JS never sees tokens. The browser must call the API
  through a SAME-ORIGIN proxy at relative /v1 (Vite dev proxy, Next.js rewrites, Vercel rewrites); send header
  `X-GB-Client: web` on every request.
- Refresh: POST /v1/auth/refresh, SINGLE-FLIGHT (one refresh at a time; on web also navigator.locks.request('gb-refresh')
  across tabs), retry the original request once. Then logout (/v1/auth/logout) and logout-all.
- Roles: after login show the role chooser from roles[]; send X-Active-Role on requests; remember the last role.
- Until the real endpoints exist, mock them with MSW using exactly these shapes (later generated from openapi.json).

=== PART 4 — PWA (apps/mobile) ===
- Two build modes: `vite build` = web PWA (API at relative /v1, hosted on Vercel with rewrites);
  `vite build --mode native` = Capacitor 8 Android (capacitor.config.json, server.androidScheme "https",
  API at the absolute Render URL from VITE_API_BASE_URL).
- vite-plugin-pwa with strategies "injectManifest" and our own src/sw.js; injectRegister: null and register the service
  worker ONLY in web mode (never inside Capacitor).
- Caching: precache the app shell; stale-while-revalidate ONLY for public catalog/image GETs; network-only for /v1/auth/*,
  user data and all writes; navigateFallbackDenylist [/^\/v1\//]; never cache responses that set cookies.
- On logout: clear service-worker runtime caches, the persisted TanStack Query cache and Dexie.
- Updates: registerType "prompt" -> in-app toast "New version available -> Refresh".
- Manifest: name/short_name "GharBasket", lang "en-IN", display "standalone", orientation "portrait",
  theme_color "#2F5D3A", background_color "#FBF7F0", id "/", 192/512 + maskable icons from the logo,
  shortcuts Week / Box / Orders.
- Install: custom "Install GharBasket" banner after the first approved box (beforeinstallprompt); iOS Safari shows
  instructions instead.
- Offline: TanStack Query cache persisted in IndexedDB; Dexie outbox with Idempotency-Key for field-role writes,
  refresh the token before replay, flush on "online" and app resume; banner "3 actions waiting to sync".
- Lazy-load the Firebase web SDK (reCAPTCHA) on web; use @capacitor-firebase/authentication on Android
  (register debug SHA-1/SHA-256 in Firebase).

=== PART 5 — NEXT TASKS AFTER THE INITIAL PR MERGES (one PR at a time) ===
1. spike/S2-firebase-otp: Firebase phone auth with test numbers on Android (native plugin) and web; choose the
   secure-storage plugin; 1-page note in docs/spikes/S-2-firebase-otp.md. DO THIS FIRST: the backend auth design waits on it.
2. feat/M0-ui-tokens: packages/ui — tokens.css from the design system, Tailwind v4 @theme, Fontsource (Fraunces, Mukta),
   base components on React Aria Components (+ Vaul sheet, Sonner toast), Storybook 10, all in JS.
3. feat/M0-mobile-shell: Vite + React 19, TanStack Router (disableTypes: true, generatedRouteTree src/routeTree.gen.js,
   zod for search params), TanStack Query + IndexedDB persister, Zustand, i18next (en-IN, hi-IN, mr-IN), the PWA spec
   above, Capacitor 8 Android, role-shell routes (customer, captain, kitchen, rider, packer), login flow
   language -> phone -> OTP -> role chooser -> home, MSW mocks per PART 3.
4. feat/M0-console-shell: Next.js 16 in JS (jsconfig.json aliases), next.config.mjs rewrites "/v1/:path*" -> the staging
   API URL + skipTrailingSlashRedirect, proxy.js session gate (redirect to /login when no session cookie),
   sidebar layout, login page.
5. spike/S1-razorpay-capacitor: Razorpay Checkout (test key id) inside the Capacitor WebView incl. UPI intent; note in
   docs/spikes/S-1-razorpay.md.
6. When Prathamesh says the auth API is live on staging: `just gen-api`, switch auth from MSW to the real API, and do the
   MS-0 demo (test-number login on the Android emulator AND in the browser PWA, role chooser, logout).

=== PART 6 — COORDINATION ===
- Daily 10-minute message to Prathamesh: done / next / blocked. Thursday demo.
- If you need a backend change or a root-config change, ask in a PR comment; don't make it yourself.
- Any change to the auth contract or API shapes needs both Yash's and Prathamesh's review.
- Start now with PART 1 only. Summarise what you found in my folder first, then do the sync, then stop.
PR at a time, ≤ ~400 lines,
Conventional Commits with module scope:
  feat/M0-ui-tokens     → packages/ui: tokens.css from the design system, Fontsource fonts, Tailwind v4 preset,
                          base components on React Aria Components, Storybook 10.
  feat/M0-mobile-shell  → apps/mobile: Vite + React 19 + TS strict, TanStack Router (file routes, role shells
                          lazy-loaded), TanStack Query, Zustand, react-hook-form + zod, i18next (en-IN/hi-IN/mr-IN),
                          vite-plugin-pwa (injectManifest), Capacitor 8 Android project.
  feat/M0-console-shell → apps/console: Next.js 16 App Router, proxy.ts auth gate stub, sidebar layout.
  spike/S1-razorpay-capacitor, spike/S2-firebase-otp-capacitor → throwaway code in spikes/,
                          1-page findings in docs/spikes/.
Rules: touch only apps/mobile, apps/console, packages/ui, packages/domain and spikes/. Never hand-edit
packages/api-client (run `just gen-api`). Don't change root configs, services/, infra/ or .github/ — ask Prathamesh
in a PR comment. The login screen uses POST /v1/auth/exchange and GET /v1/me exactly as in section 3.1 of
docs/modules/M1-identity.md; mock them until the backend lands. Verify library APIs with Context7 before coding.
Ask Yash before any decision the docs don't cover.
```

## 6. Daily sync with Prathamesh
- A daily 10-minute message: done, next, blocked.
- A Thursday demo.
- Any change to a shared contract (API shapes, tokens used by the console, root configs) needs the other person's review on the PR.
