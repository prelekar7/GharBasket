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

PHASE B — CODE (only after Prathamesh's "chore/M0-scaffold" PR is merged to main), one PR at a time, ≤ ~400 lines,
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
