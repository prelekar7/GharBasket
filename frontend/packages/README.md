# frontend/packages

JavaScript packages shared by `frontend/mobile` and `frontend/console`.

| Folder | What | Owner |
|---|---|---|
| `ui/` | Design tokens, fonts, Tailwind v4 theme, components (React Aria), Storybook | Yash Salunkhe |
| `api-client/` | `src/generated/schema.d.ts` is **generated** from `backend/api/openapi.json` by `just gen-api` (never edit by hand); `src/index.js` is the small openapi-fetch runtime (ADR-0022) | generated; runtime reviewed by both owners |
| `domain/` | Shared constants not in OpenAPI (role keys, feature flags) | Yash Salunkhe |
