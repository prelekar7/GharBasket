# packages

Shared TypeScript packages used by both apps.

| Folder | What | Owner |
|---|---|---|
| `ui/` | Design tokens, fonts, Tailwind preset, components (React Aria), Storybook | Yash Salunkhe |
| `api-client/` | **Generated** from the FastAPI OpenAPI spec by `just gen-api`. Never edit by hand | generated |
| `domain/` | Shared enums and constants not in OpenAPI (role keys, feature flags, i18n key types) | Yash Salunkhe |
| `config/` | Shared tsconfig presets | Prathamesh Relekar |
