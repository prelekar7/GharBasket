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
