# Design artifacts

All UI design is done in Claude Design (ADR-0016) and was reviewed and approved by the team in Phase 0 (ADR-0017). The artifacts are private: open them with the GharBasket Claude account, or ask Yash Salunkhe to share them.

| Artifact | Link | Version |
|---|---|---|
| GharBasket · Design System (tokens, type, 58 components incl. field-role and console groups, logos, 40 icons, 6 illustrations) | https://claude.ai/artifact/F61C7MsjLTzcmN57zHoSMD | v17 (4 Oct 2026) |
| GharBasket · Customer screens (wireframes and hi-fi, English and Hindi) | https://claude.ai/artifact/LHGfT7GN8dzwKHGjG21Hba | v26 · `1791261392-72c1` |
| GharBasket · Field-role screens (packer, rider, captain, kitchen; English and Hindi) | https://claude.ai/artifact/Cw6CEKqTsbrPLc7nnsu3fd | v12 (4 Oct 2026) |
| GharBasket · Console screens (staff console, English) | https://claude.ai/artifact/JHy5HbTLBG38HMo9nZQ58d | v15 (4 Oct 2026) |

## What is on the canvases
### Customer screens
| Page | Screens |
|---|---|
| Wireframes | Onboarding: language → OTP → society → tower/flat → household stepper (5 steps) → consent → first week → payment → done |
| Hi-fi onboarding | The same 15 screens, English and Hindi |
| Wireframes · core screens | Week, Box (parchi), substitute, quantity, price spike; wallet, top-up, confirm, payment failed; society live/unlocking/share/unlocked; orders, Kindness, profile, become a partner, assistant |
| Hi-fi core screens | Week, Box, Substitute sheet, Wallet, Top-up, Society unlock; Orders and tracking, Kindness, Profile (senior mode), Become a partner, Ghar Saathi. English and Hindi |
| Hi-fi · more customer screens | Day, approved, skipped and payment-failed weeks; quantity, price spike, add item, product; confirm top-up, statement, Autopay, Budget Lock; tracking, rate, tiffin; society live, share, unlocked; household, change flat, notifications and their settings, help, legal, delete data; partner documents and quiz; impact |

### Field-role screens (390 × 844)
25 screens: packer (queue, pick, shortages, scan error, loading, done), rider (route, stop, pickups, earnings, no route, error), captain (today, handover, flats, exceptions, earnings, manual entry, done) and kitchen (today, prep, menu, training, earnings, no tiffins). Every scan has a typed-code fallback.

### Console screens (1440, works at 390)
52 artboards on seven pages: MS-A admin (login, catalog, product edit, ⌘K, prices, publish, societies map, society requests, users and roles, festivals, tiffin menus, plans, hubs), MS-A operations (orders, cut-off, slots, cycles, delivery days, reconciliation, refunds, payouts, templates, delivery log), MS-B hub operations (ops board, packing, procurement, receiving, vendors, waste, routes, deliveries, vehicles, partners, partner profile, fairness), MS-C intelligence (AI health, experiments), MS-D community (drives, donations, impact, hand-offs), franchise and analytics (P&L, unit economics, staff, customers, KPIs, cohorts, settings) and public pages (landing, privacy, society invite, know your cook).

Every hi-fi screen has a Dark mode Tweak; customer and field screens also have a Language Tweak (en / hi). People, societies and hubs on the screens are the backend seed (`backend/api/src/gharbasket/tools/seed.py`); counts and money on the field and console screens are examples, and the canvases say so.

## From design to code
1. Tokens change in the design system first, never in code.
2. Export the design system's `project/tokens.json`, then run
   `pnpm --filter @gharbasket/ui sync-tokens <path/to/tokens.json> <version id>` to regenerate `frontend/packages/ui/src/tokens.css`.
3. Tailwind utilities come only from those tokens (`frontend/packages/ui/src/theme.css` turns off Tailwind's default palette), for example `bg-surface`, `text-fg`, `bg-primary text-on-primary`, `rounded-md`, `shadow-focus`.
4. Components are rebuilt in `frontend/packages/ui` on React Aria Components, in JavaScript with JSDoc (ADR-0018, proposed), and documented in Storybook (`pnpm --filter @gharbasket/ui storybook`).

## Decisions taken during design review (2 Oct 2026)
- First-week preview comes **before** payment in onboarding (as in workflow W1).
- Add-ons are lines on the weekly box and are paid in the same cut-off payment (resolves the M3 open question).
- `ink-500` darkened from `#7a7266` to `#6f675b` so muted text passes WCAG AA on both kraft backgrounds (5.2:1 and 4.7:1).
- Hindi headings use Mukta Bold, because Fraunces has no Devanagari.
- Senior mode raises type two steps everywhere, including the bottom tab bar (`TabBar senior`).
- Hindi copy is a first draft; Rhythm Thakur and Manish Raje review it before usability tests.

## Decisions taken while designing field-role and console screens (4 Oct 2026)
- Field and console examples use only the two live seed societies (Sunrise Towers, Krishna Kunj) and one shared story day: Tue 6 Oct, Kharghar hub, 58 boxes (46 + 12).
- The five field components (BagLabel, CrateCounter, RouteStopCard, FlatChecklist, ShortageReporter) and the console components (DataTable, FilterBar, KpiTile, ChartCard, MapPanel, PolygonEditor, KanbanBoard, PrintableLabels) live in the design system first; code follows in `frontend/packages/ui` (console ones behind `@gharbasket/ui/console`).
- The console is English only for now; field screens are English and Hindi like the customer app. The canvases have no Marathi; the apps' `mr-IN` strings are drafts that the Hindi and Marathi review covers.
