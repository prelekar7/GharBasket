## What and why
<!-- One or two sentences. Link the module spec (docs/modules/Mx-*.md) or issue. -->

Module: <!-- e.g. M1 identity, M0 platform, mobile, console -->

## How I verified it
<!-- Commands you ran and what you saw. Screenshots for UI changes (light/dark, English/Hindi). -->

## Checklist
- [ ] Small and focused (about 400 changed lines or fewer)
- [ ] Only touches folders my track owns (see `AGENTS.md` / `.github/CODEOWNERS`), or the owner agreed in a comment
- [ ] Tests added or updated; CI is green
- [ ] No secrets, `.env` files, keystores or real personal data
- [ ] API change? `openapi.json` and `packages/api-client` regenerated, and both owners reviewed
- [ ] Database change? one Alembic migration, named `YYYYMMDD_HHMM_<module>_<desc>`, downgrade works
- [ ] Docs or the handbook chapter updated if behaviour changed
