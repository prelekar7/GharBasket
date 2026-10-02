# Design artifacts

All UI design is done in Claude Design (ADR-0016) and was reviewed and approved by the team in Phase 0 (ADR-0017). The artifacts are private: open them with the GharBasket Claude account, or ask Yash Salunkhe to share them.

| Artifact | Link | Version at hand-off |
|---|---|---|
| GharBasket · Design System (tokens, type, 36 components, logos, icons, illustrations) | https://claude.ai/artifact/F61C7MsjLTzcmN57zHoSMD | v12 · `1790953737-3da3` |
| GharBasket · Customer screens (wireframes and hi-fi, English and Hindi) | https://claude.ai/artifact/LHGfT7GN8dzwKHGjG21Hba | v13 · `1790953778-214f` |

## What is on the canvas
| Page | Screens |
|---|---|
| Wireframes | Onboarding: language → OTP → society → tower/flat → household stepper (5 steps) → consent → first week → payment → done |
| Hi-fi onboarding | The same 15 screens, English and Hindi |
| Wireframes · core screens | Week, Box (parchi), substitute, quantity, price spike; wallet, top-up, confirm, payment failed; society live/unlocking/share/unlocked; orders, Kindness, profile, become a partner, assistant |
| Hi-fi core screens | Week, Box, Substitute sheet, Wallet, Top-up, Society unlock; Orders and tracking, Kindness, Profile (senior mode), Become a partner, Ghar Saathi. English and Hindi |

Every hi-fi screen has two Tweaks on the canvas: Language (en / hi) and Dark mode. Field-role and console canvases come at the start of P2/P3 (ADR-0017).

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
