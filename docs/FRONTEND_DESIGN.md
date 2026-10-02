# GharBasket — Frontend Design Document (FDD)

| | |
|---|---|
| **Version** | 1.0 (approved baseline, 2 Oct 2026) |
| **Owners** | Design execution in Claude Design; UX research, copy and usability testing: Rhythm Thakur, Manish Raje; buildability review and implementation in `frontend/packages/ui`: Yash Salunkhe |
| **Related** | `docs/TDD.md` (Sections 3.2–3.4, 9) · `docs/handbook/09-frontend-and-design.md` (screen-flow diagrams) · ADR D16/D17 |

---

## Table of contents
1. [Design principles](#1-design-principles)
2. [Brand](#2-brand)
3. [Colour system](#3-colour-system)
4. [Typography](#4-typography)
5. [Iconography](#5-iconography)
6. [Illustration and imagery](#6-illustration-and-imagery)
7. [Motion](#7-motion)
8. [Navigation and layout](#8-navigation-and-layout)
9. [Component inventory](#9-component-inventory)
10. [Screen inventory](#10-screen-inventory)
11. [Interaction patterns](#11-interaction-patterns)
12. [Content, localisation and accessibility](#12-content-localisation-and-accessibility)
13. [Design workflow (Claude Design)](#13-design-workflow-claude-design)
14. [Design timing](#14-design-timing)
15. [Frontend libraries](#15-frontend-libraries)
16. [Quality checklist for UI pull requests](#16-quality-checklist-for-ui-pull-requests)

---

## 1. Design principles

1. **Plan-first, not shop-first.** The home screen shows the family's week, not a catalog grid.
2. **Indian kitchen warmth.** Kraft paper, turmeric, the handwritten *parchi* (shopping slip), the *thali* — familiar rather than "tech startup".
3. **Trust through transparency.** Every AI choice carries a reason chip; every tiffin shows who cooked it ("Know your cook").
4. **Inclusive by default.** Works equally for a 68-year-old Marathi speaker and a 27-year-old gym-goer: senior mode, large targets, assistant entry point.
5. **Field roles are tools, not apps.** Captain, rider and packer screens are scan-first, one-handed, offline-tolerant, with large buttons and haptic feedback.

## 2. Brand

- **Logotype:** bilingual wordmark **"घरBasket"** — Devanagari "घर" in Mukta Bold with "Basket" in Fraunces. Icon: a simple basket formed by a handwritten tick (the "approve" gesture).
- **Voice:** warm, neighbourly Hinglish/Marathi; never pushy or salesy.
  - Use: "Aapka hafte ka khana ready hai — ek baar dekh lijiye."
  - Avoid: all-caps deal language, exclamation stacks, emoji-heavy copy.
- **Taglines:** "Ghar ka khana, bina tension." / "Your kitchen, planned."

## 3. Colour system

Primitive tokens → semantic tokens → role accents. All foreground/background pairs meet WCAG AA.

| Token | Hex | Use |
|---|---|---|
| `kraft-50` / `kraft-100` | `#FBF7F0` / `#F3EBDD` | App background, cards |
| `ink-900` / `ink-700` / `ink-500` | `#1E1B16` / `#4A443B` / `#7A7266` | Text hierarchy |
| `turmeric-500` | `#E8A33D` | Primary accent, highlights |
| `leaf-700` | `#2F5D3A` | Primary actions, success |
| `chilli-600` | `#C8442B` | Errors, price-spike warnings |
| `neel-600` | `#2E4A7D` | Information, links |
| `slate-500` | `#5E6B73` | Neutral UI |

**Role accents** (thin top bar and tab-icon tint show the active role, so a person with two roles always knows the current mode):

| Role | Accent |
|---|---|
| Customer | leaf |
| Society Captain | turmeric |
| Kitchen partner | chilli |
| Rider | neel |
| Packer | slate |

**Dark mode:** warm charcoal `#1A1714` surfaces; accents desaturated about 10 %.

## 4. Typography

- **Fraunces** (variable; "SOFT" axis for friendly curves) for display headings and hero numbers.
- **Mukta** for all UI text — Latin and Devanagari designed together, so Hindi and Marathi do not look like a fallback.
- Prices use `font-variant-numeric: tabular-nums`.
- Scale (px): 12 · 14 · 16 (body) · 18 · 22 · 28 · 36. **Senior mode** = +2 steps, line-height 1.6 for Devanagari.
- Self-hosted via Fontsource (works offline inside Capacitor; no runtime call to Google Fonts). Subset to Latin + Devanagari.

## 5. Iconography

- **Phosphor Icons** (`@phosphor-icons/react`): `regular` weight for UI; `duotone` for category tiles with a turmeric or leaf second tone. Chosen over Lucide, which is the default in most AI-generated interfaces.
- **Custom GharBasket icon set (~40 SVGs)** on the same 1.5 px stroke grid: vegetable types, thali, tiffin dabba, kadai, gullak, crate, society tower, captain badge, weighing scale. Delivered as React components via SVGR.
- **Rule:** no emoji as icons in product UI.

## 6. Illustration and imagery

- **Photography:** real produce shot by the team (natural light, kraft-paper backdrop, top-down) for a consistent catalog; real partner portraits with written consent.
- **Spot illustrations** for onboarding and empty states: hand-drawn ink line with turmeric fill.
- **Not used:** unDraw, Storyset, 3D blobs, generic stock or generated stock imagery.
- **Delivery:** AVIF/WebP from S3 with generated thumbnails; thumbhash placeholders.

## 7. Motion

- Purposeful, at most 250 ms, spring-based; honours `prefers-reduced-motion`.
- **Motion** for layout transitions (item swap in the Parchi, sheet content). **View Transitions** for page navigation (TanStack Router `viewTransition`).
- **NumberFlow** for money and counters: wallet balance, bill total, society-unlock progress, Kindness points.
- **Rive** for three brand moments only: the gullak filling when Kindness points arrive; the basket-packing loader; the society-unlock celebration.
- Haptics (Capacitor) on scan success/failure and on approve.

## 8. Navigation and layout

**Mobile (bottom tabs per role)**

| Role | Tabs |
|---|---|
| Customer | Week (home) · Box · Orders · Kindness · Profile; floating "Ghar Saathi" assistant button |
| Captain | Today · Scan · Flats · Earnings |
| Kitchen partner | Today · Menu · Prep · Earnings |
| Rider | Route · Stop · Pickups · Earnings |
| Packer | Queue · Pick/Pack · Shortages |

- Editing uses **bottom sheets** (Vaul) with snap points rather than full-page forms.
- Role switcher lives in Profile and in the header when a user holds more than one role.

**Console:** left sidebar grouped by Admin / Hub / Franchise / Analytics; top bar with hub switcher; ⌘K command palette (cmdk); dense data tables; map panels.

## 9. Component inventory

All components live in `frontend/packages/ui`, are built on React Aria Components, styled with our tokens, and documented in Storybook 10.

**Base:** Button, IconButton, TextField, NumberField, Select / ComboBox, Checkbox, Switch, RadioCards, Slider, QtyStepper, Tabs, SegmentedControl, Sheet (Vaul), Dialog, Toast (Sonner), Tooltip, Popover, Badge, StatusPill, Avatar, Skeleton, ProgressBar, Chip, DatePicker (Indian locale), EmptyState, ErrorState, OfflineBanner.

**Signature (domain):** `ParchiList` / `ParchiLine` (handwritten-slip box with reason chips), `ThaliWeek` (seven-day meal calendar), `MealCard`, `SubstituteSheet` (three ranked substitutes with price delta), `PriceSwapChip`, `MoneyText`, `WalletCard`, `GullakMeter`, `SocietyUnlockBar`, `KnowYourCookCard`, `OrderTimeline`, `RoleSwitcher`, `ConsentCard`.

**Field roles:** `ScanButton` (full width, haptic), `BagLabel`, `CrateCounter`, `RouteStopCard`, `FlatChecklist`, `ShortageReporter`.

**Console:** `DataTable` (TanStack Table + Virtual), `FilterBar` (nuqs), `KpiTile`, `ChartCard` (ECharts theme), `MapPanel` (MapLibre + deck.gl), `PolygonEditor` (terra-draw), `KanbanBoard` (packing), `PrintableLabels` (bag QR sheets).

## 10. Screen inventory

About 40 screens in v1.

**Customer (18):** Welcome / Language · Login (OTP) · Society picker · Household setup (stepper) · Consent · Payment mode · **Week (Thali)** · **Box (Parchi)** · Substitute sheet · Add-ons · Tiffin booking · Orders + live tracking · Wallet and top-up · Kindness (gullak, schedule donation) · Society (unlock, captain, referrals) · Profile / Settings (senior mode, language, devices, data export/delete) · Become a partner · Assistant chat.

**Captain (5):** Today · Handover scan · Flat checklist · Exceptions · Earnings.

**Kitchen partner (5):** Today counts · Menu editor · Prep status · Training · Earnings.

**Rider (4):** Route map · Stop detail + proof · Pickups · Earnings.

**Packer (3):** Queue · Pick/Pack scanner · Shortages.

**Console (~16):** Login · Ops board (today) · Procurement · Receiving / QC · Packing kanban · Routes live map · Deliveries · Waste and cascade · Catalog and prices · Societies map editor · Partners and KYC review · Drives and NGOs · Analytics / KPIs · Unit economics · AI health · Settings. Public: landing page and society referral pages (`/s/<society>`).

## 11. Interaction patterns

- One primary action per screen (for example, "Approve this week").
- Undo snackbars instead of "are you sure?" dialogs, except for money.
- Optimistic updates with rollback.
- Skeletons for content loading; spinners only for in-button actions.
- Pull-to-refresh on lists.
- **Explain before asking:** consent screens say why each data point is needed.
- Field roles: scan first, manual entry as fallback; offline queue indicator ("3 actions waiting to sync").
- Errors: human-readable message, retry, and support link; never raw error codes.

## 12. Content, localisation and accessibility

- All copy in i18n files (`en-IN`, `hi-IN`, `mr-IN`); translations and copy review owned by Rhythm Thakur and Manish Raje.
- Indian number and date formats (₹1,23,456; "Shukravar, 9 Oct"); local units ("1 judi methi").
- WCAG 2.2 AA, 48 px touch targets, React Aria labelling, colour never the only signal, senior mode, reduced motion.
- Verified with TalkBack on Android and axe in CI.

## 13. Design workflow (Claude Design)

UI design is done entirely in **Claude Design** (decision D16).

1. **UX research** (Rhythm Thakur, Manish Raje): interviews with 8–10 households, 3 home cooks and 2 society secretaries → personas and journey maps.
2. **GharBasket Design System artifact** — the single source of truth for tokens (colour, type, spacing, radius, motion), role accents, icon style, and base + signature components.
3. **Design canvases** attached to the design system:
   - *Customer* canvas,
   - *Field roles* canvas (captain, kitchen, rider, packer),
   - *Console* canvas.
   Each moves from wireframe artboards → quick test with 3–5 people → high-fidelity artboards.
4. **Brand assets** (logo, ~40-icon set, spot illustrations) are also drafted on the canvas with explicit art direction: hand-drawn ink line, turmeric fill, 1.5 px stroke grid. Iterate until distinctive.
5. **Review** through artifact comments by the whole team; Yash Salunkhe checks buildability against the component inventory.
6. **Hand-off to code:** tokens from the design system are copied into `frontend/packages/ui/tokens.css` (CSS variables) and the Tailwind preset. Any token change is made in the design system first, then synced to code. Components are built in Storybook 10 with the a11y addon; Playwright screenshot tests guard against visual regressions.
7. All design artifacts start private; links are shared with the team and Prof. Kranti Bade.

## 14. Design timing

| When | What is designed |
|---|---|
| **Phase 0 (before any code)** | Design System; customer core screens: onboarding, Week/Thali, Box/Parchi, Substitute sheet, Wallet, Society unlock; logo and icon-set drafts |
| Start of Phase 2 | Remaining customer screens; console catalog, prices and societies screens |
| Start of Phase 3 | Field-role screens (captain, kitchen, rider, packer); console operations screens |
| Start of Phase 4 | Autopilot explanations, Budget Lock, assistant chat; AI health page |
| Start of Phase 5 | Kindness, referrals, impact dashboard |

## 15. Frontend libraries

| Purpose | Library | Used in |
|---|---|---|
| Framework | React 19; Vite (mobile); Next.js 16 (console) | Both |
| Routing | TanStack Router (mobile); App Router (console) | |
| Server state | TanStack Query + Hey API generated hooks | Both |
| Client state | Zustand | Mobile |
| Forms / validation | react-hook-form + zod | Both |
| Styling | Tailwind CSS v4, tailwind-variants, tailwind-merge, clsx | Both |
| Accessible primitives | React Aria Components (+ @internationalized/date) | Both |
| Icons | @phosphor-icons/react + custom SVG set (SVGR) | Both |
| Bottom sheets | Vaul | Mobile |
| Toasts | Sonner | Both |
| Animation | Motion, @number-flow/react, @rive-app/react-canvas, View Transitions | Mobile (light use in console) |
| Carousel | Embla Carousel | Mobile |
| Lists / tables | TanStack Virtual; TanStack Table | Both / console |
| Command palette | cmdk | Console |
| URL state | nuqs | Console |
| Charts | Apache ECharts | Console |
| Maps | maplibre-gl, react-map-gl (MapLibre), deck.gl, terra-draw | Both / console |
| Images | thumbhash; S3-generated thumbnails | Both |
| QR and labels | qrcode.react, @react-pdf/renderer | Console |
| Fonts | Fontsource (Fraunces variable, Mukta) | Both |
| i18n | i18next + react-i18next + ICU | Both |
| Native | Capacitor 8 + plugins (see TDD 3.2) | Mobile |
| Offline | Dexie | Mobile |
| PWA | vite-plugin-pwa | Mobile |
| Component workshop | Storybook 10 + addon-a11y | `frontend/packages/ui` |
| Testing | Vitest, Testing Library, Playwright | Both |
| Monitoring | Sentry, PostHog | Both |

**On shadcn/ui:** components are not installed as-is, because that produces the generic look we want to avoid. We follow the same ownership idea — our component code lives in `frontend/packages/ui`, built on React Aria and styled with our tokens.

**Banned in product UI:** purple/blue gradients, glassmorphism, emoji as icons, default shadcn or Material look, stock 3D illustrations, generic "AI sparkle" icons.

## 16. Quality checklist for UI pull requests

- [ ] Matches the approved Claude Design artboard (screenshot attached).
- [ ] Light and dark mode screenshots; English and Hindi screenshots.
- [ ] Uses tokens only (no hard-coded colours or font sizes).
- [ ] Keyboard and screen-reader labels via React Aria; axe passes.
- [ ] Touch targets ≥ 48 px; senior mode checked.
- [ ] Loading (skeleton), empty, error and offline states implemented.
- [ ] Copy is in i18n files; no hard-coded strings.
- [ ] Performance budget respected (bundle analyser output for new routes).
