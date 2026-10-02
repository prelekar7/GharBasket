# Developer setup

Everyone installs the same toolchain so `just up && just dev` works on every laptop. Rhythm Thakur and Manish Raje verify this checklist on their machines in Sprint 0 and keep it current.

## 1. Tools

| Tool | Version | macOS (Homebrew) | Windows |
|---|---|---|---|
| Git | latest | `brew install git` | Git for Windows |
| Node.js | 24 LTS (see `.nvmrc`) | `brew install fnm && fnm install 24 && fnm use 24` | `winget install Schniz.fnm` |
| pnpm | 12 (pinned in `package.json`) | `corepack enable` (ships with Node) | `corepack enable` |
| Python | 3.13 (see `.python-version`) | `uv python install 3.13` | `uv python install 3.13` |
| uv | latest | `brew install uv` | `winget install astral-sh.uv` |
| just | latest | `brew install just` | `winget install Casey.Just` |
| Docker Desktop | latest | Docker Desktop for Mac | Docker Desktop (WSL 2 backend) |
| Android Studio | latest | Android Studio + SDK + one emulator (API 34+) | same |
| gitleaks (optional locally; CI always runs it) | latest | `brew install gitleaks` | `winget install gitleaks` |
| GitHub CLI | latest | `brew install gh && gh auth login` | `winget install GitHub.cli` |

Windows users: clone inside WSL 2 for the backend, so the bash scripts and the `justfile` work unchanged.

## 2. First run
```bash
git clone https://github.com/prelekar7/GharBasket
cd GharBasket
just install          # pnpm install (also installs git hooks) + uv sync
just                  # lists every command
```
After `chore/M0-infra-compose` merges: `just up` starts Postgres, Valkey, MinIO and MLflow.
After `feat/M0-api-platform` merges: `just migrate` then `just dev`.

## 3. Editor
- VS Code or Cursor with: Biome, Python, Ruff, Pylance, Tailwind CSS IntelliSense, EditorConfig.
- Format on save is handled by Biome (TypeScript/JSON/CSS) and Ruff (Python).

## 4. Accounts (shared project owner; credentials in the team password manager, never in git)
AWS (ap-south-1 Mumbai), Neon, Firebase (Phone Auth + FCM), Razorpay (test mode), Sentry, PostHog, Grafana Cloud, Anthropic (from Sprint 8). Local secrets go in `.env` (git-ignored), copied from `.env.example`.

## 5. AI assistants
`AGENTS.md` is the single source of instructions for every AI tool. Edit only that file, then run `just sync-agent-docs`. The pre-commit hook and CI reject out-of-sync copies.
