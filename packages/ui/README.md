# @gharbasket/ui

GharBasket's design tokens, fonts, Tailwind v4 theme and components, built on React Aria Components. Owner: Yash Salunkhe. The design source of truth is the Claude Design system listed in [`docs/design/README.md`](../../docs/design/README.md).

```js
import "@gharbasket/ui/styles.css"; // once, in the app root: Tailwind + fonts + tokens + theme
import { Button } from "@gharbasket/ui";
```

| File | What |
|---|---|
| `src/tokens.css` | Generated `--gb-*` CSS variables, light and dark (`data-theme`, or the OS setting). Never edit by hand |
| `src/theme.css` | Maps the tokens to Tailwind utilities; Tailwind's default colours, fonts, radii and shadows are off |
| `src/styles.css` | Entry stylesheet: Tailwind, Fraunces (variable, all axes incl. SOFT) and Mukta (400–700, Latin + Devanagari), tokens, theme |
| `src/components/*.jsx` | Components in JavaScript with JSDoc types (ADR-0018) |
| `scripts/sync-tokens.js` | Regenerates `tokens.css` from the design system's `tokens.json` |

Commands (from the repo root): `pnpm --filter @gharbasket/ui storybook` · `… build` · `… typecheck` · `… sync-tokens <tokens.json> <version>`.

Fonts are self-hosted through Fontsource, so they work offline inside Capacitor; nothing is loaded from Google Fonts at runtime.
