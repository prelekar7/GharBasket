<!-- Canonical AI-assistant instructions. Edit THIS file only, then run `just sync-agent-docs`
     to copy it into CLAUDE.md, GEMINI.md, CODEBUDDY.md, QODER.md, .cursorrules and .windsurfrules. -->
# GharBasket — instructions for AI assistants and contributors

GharBasket is a 4-person B.Tech major project (SIES GST, guide Prof. Kranti Bade): a "Kitchen Autopilot" that plans an
Indian family's food week, with women "Society Captains" running the last 50 metres and scheduled, zero-waste delivery.

## Read first
1. `docs/handbook/00-README.md`, then the chapter for your task (architecture: 04, data: 05, lifecycles: 06,
   workflows: 07, AI: 08, frontend: 09, API: 10, engineering process: 11).
2. The module spec you are working on: `docs/modules/Mx-*.md`. Specs come before code.
3. Decisions in `docs/adr/` are final. To change one, write a new ADR that supersedes it; never change course silently.
4. Verify current library APIs with Context7 before using them; do not rely on memory for versions or signatures.

## Architecture rules (non-negotiable)
- Service-based, not microservices: `backend/api` (FastAPI modular monolith), `backend/ml` (ML + optimisation),
  Taskiq workers, `frontend/mobile` (Vite + React → PWA + Capacitor 8), `frontend/console` (Next.js 16).
- One module = one owner = one folder = one Postgres schema. A module writes only to its own schema.
  Cross-module calls go only through `<module>/api.py` or outbox events. import-linter enforces this in CI.
- Money is integer paise (`BIGINT`, `amount_paise`), never floats. Every money movement is a double-entry ledger entry.
- Every POST that creates money or orders requires an `Idempotency-Key`.
- Errors use RFC 9457 `application/problem+json` with a namespaced `code` (e.g. `orders.cutoff_passed`).
- ML never blocks a customer: online ML calls have timeouts and rule-based fallbacks.
- The LLM never computes prices or quantities; solvers and statistics decide, the LLM explains and talks.
- Personal data: no Aadhaar storage; health tags only with recorded consent (DPDP).

## Folder ownership
| Path | Owner |
|---|---|
| root configs, `infra/`, `backend/`, `data/`, `.github/`, `scripts/` | Prathamesh Relekar |
| `frontend/mobile`, `frontend/console` (UI), `frontend/packages/ui`, `frontend/packages/domain` | Yash Salunkhe |
| `frontend/packages/api-client` | generated only (`just gen-api`), never hand-edited |
| `e2e/`, `docs/` test plans and report material | Rhythm Thakur, Manish Raje |

## Commands
`just` lists everything. Main ones: `just install`, `just up`, `just dev`, `just test`, `just lint`, `just fmt`,
`just migrate`, `just gen-api`, `just sim`, `just sync-agent-docs`.

## Git and pull requests
- Trunk-based: branch from `main`; names like `feat/M4-cutoff-lock`, `fix/M5-webhook-idempotency`, `chore/…`, `spike/…`.
- Conventional Commits with module scope: `feat(orders): lock orders at cut-off`.
- Small PRs (about 400 changed lines or fewer), squash merge, CODEOWNER review, CI green.
- One Alembic migration per PR, named `YYYYMMDD_HHMM_<module>_<desc>`; keep a single migration head.
- Never commit secrets or `.env` files. Update the relevant handbook chapter when behaviour changes.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**This project has a knowledge graph. Start with the code-review-graph
MCP tools to narrow scope, then read the source.** The graph is cheaper than scanning files and
gives you structural context (callers, dependents, test coverage) that file search cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from graph output alone.
- For any non-trivial change, read the implementation and the relevant tests before concluding.
- Verify the exact source when touching behavior, database logic, migrations, retries, fallbacks,
  recovery, or compatibility code.
- When the graph and the source disagree, the source wins. The graph may be stale or may not
  model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not "does not exist".

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
<!-- /code-review-graph MCP tools -->
