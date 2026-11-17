# ADR-0038 — Row-Level Security waits until after the pilot; the ownership checks in code are enforced by tests

- **Status:** Proposed
- **Date:** 2026-11-15
- **Decision ID:** D38
- **Would supersede in part (once Accepted):** [ADR-0006](0006-postgresql-centric-data-platform.md), its words "and **Row-Level Security** for franchise isolation". The rest of ADR-0006 stands. When this ADR is accepted, ADR-0006's status line becomes "Accepted (superseded in part by ADR-0038)". Until then ADR-0006 is unchanged.
- **Relates to:** [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) (Neon), [ADR-0021](0021-jobs-without-a-broker.md) (the outbox relay and the job endpoints), [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) (the read-only training role), [ADR-0036](0036-production-environment-for-the-pilot.md) (the production Neon project). Handbook 13 §13.4, handbook 10 §10.2, handbook 05 §5.9, M0 §9. ASVS L1 first pass (PR #568, `docs/security/asvs-l1-2026-11.md`), finding F1.

## Context

### What the documents say, and what exists
Handbook 13 §13.4, handbook 10 §10.2, handbook 05 §5.9, TDD §4.2 and ADR-0006 describe Postgres Row-Level Security (RLS) as defence in depth. They say it covers the hub-scoped tables, with `SET LOCAL app.hub_ids = '…'` set per transaction. M0 §9 lists an integration test, "RLS session variable is set per transaction". M1, M7, M8 and M12 refer to it.

None of this was built. This was checked on `main` at `5d7976ed4`:
- None of the 95 migrations in `backend/api/migrations/versions/` runs `ENABLE ROW LEVEL SECURITY` or `CREATE POLICY`.
- `platform/db.py` never sets `app.hub_ids`.
- `modules/identity/permissions.py:3` says "hub managers read them once row-level security arrives".
- M12 §3.2 already says so: "no table in the monolith has RLS yet".

Every object endpoint checks ownership or scope in code instead. The ASVS pass sampled 24 of them, and all 24 were correct. `Permit.ensure(scope_id)` (`platform/permissions.py:121-131`) is the hub, society or NGO check, and each module checks a customer's ownership itself.

### What RLS would have to cover
These counts come from the models.

| Kind of table | Examples | Count |
|---|---|---|
| Hub-scoped, with a `hub_id` column | `orders.orders`, `orders.slots`, `hub_ops.inventory_lots`, `hub_ops.purchase_orders`, `logistics.routes`, `logistics.deliveries`, `partners.partners`, `partners.assignments`, `community.societies`, `catalog.price_lists` | about 50, 11 of them `analytics` fact tables |
| Children of hub-scoped tables, with no `hub_id` | `orders.order_lines`, `orders.order_events`, `hub_ops.packing_lines`, `logistics.route_stops`, `logistics.stop_orders`, `partners.assignment_orders`, `hub_ops.*_lines` | about 12 |
| Owned by a user or household, with no hub | `identity.addresses`, `identity.household_members` (health tags), `identity.sessions`, `partners.kyc_documents`, `billing.payments`, `billing.refunds`, `subscriptions.subscriptions`, `assistant.sessions`, `notifications.preferences` | about 30 |
| Not scoped (platform, catalog, ledger) | `billing.postings`, `billing.journal_entries`, `catalog.products`, `platform.outbox` | the rest |

Two of the documents' examples are wrong. `partners.kyc_documents` has `user_id` and no `hub_id`, so M8's "RLS by `hub_id`" on it cannot work as written. The analytics `mv_*` are materialized views, and Postgres has no RLS for materialized views at all.

### How the API reaches the database
- **One role for everything.** As far as the repo shows, `DATABASE_URL` (pooled, the app) and `DATABASE_URL_DIRECT` (migrations, backups) are the same Neon role, the project's owner. `scripts/neon-ro-role.sql` says it runs "as the database owner (the role the migrations run as)". So the API connects as the **owner of every table**. Locally it connects as the Docker `gharbasket` user, which is a **superuser**.
- **Neon's owner skips RLS entirely.** On Neon, every role made in the Console, CLI or API is a member of `neon_superuser`, and that includes the project's default role. `neon_superuser` has `BYPASSRLS`, `pg_read_all_data` and `pg_write_all_data` (Neon docs, "The neon_superuser role"). A `BYPASSRLS` role skips policies even with `FORCE ROW LEVEL SECURITY`. So with today's connection, RLS would change nothing on staging, even with every policy written. Only a role made **with SQL** starts with no such rights. `gharbasket_ro` (ADR-0025) is made that way.
- **Sessions and transactions.** `get_session` opens one `AsyncSession` per request. Services commit for themselves: there are 185 `session.commit()` calls, and some requests commit more than once. Each commit ends the transaction, and `SET LOCAL` with it, so the variable would have to be set again at the start of **every** transaction (a SQLAlchemy `after_begin` hook), not once per request.
- **PgBouncer.** The app goes through Neon's pooled endpoint, which is PgBouncer in transaction mode (`platform/db.py`). `SET LOCAL` and `set_config(…, true)` work there, because they last only for the transaction. A plain `SET` would leak to the next client of the server connection. A test already shows the `SET LOCAL` behaviour for `statement_timeout` (`tests/analytics/test_kpis.py:672`). `SET LOCAL` cannot take a bind parameter, so the value would go through `SELECT set_config('app.hub_ids', :ids, true)`.
- **Work with no `Permit`.** The outbox relay (`platform/outbox.py:169`, after each commit, across every hub), the job endpoints (`/v1/internal/jobs/*`, one operator token, for example cut-off for every hub), `logistics/jobs.py:180`, the consumers that write into other modules' tables, and `REFRESH MATERIALIZED VIEW CONCURRENTLY` (`analytics/refresh.py:539`) all run in the same process with the same engine. Every one of them would need a bypass.
- **Customers are not hub-scoped.** A customer has no hub grant: they reach their own household, orders and wallet by ownership. A policy on `hub_id` alone therefore protects nothing between two customers. Yet a customer reaching another customer's order by id is the likeliest kind of authorisation bug (OWASP API1, BOLA). Protecting that would need a second policy keyed on the caller's household ids, which means one more lookup per request.
- **The training role.** `gharbasket_ro` reads `orders`, `subscriptions`, `catalog`, `intel` and two household tables. It was made with SQL, so it has no `BYPASSRLS`. Once RLS is on, it would read **no rows** unless it gets its own policy, and the training jobs would fail without an error.

### The pilot
The pilot is one hub with one or two Panvel societies (handbook 14, P6 and S10). The seed world also has a single hub (`tests/hub_ops/test_hubs.py:274`). With one hub there is no second franchise to keep apart. RLS on `hub_id`, which is the design the documents describe, isolates nothing during the pilot.

## Options

### A. Build hub RLS now
Enable RLS with `FORCE` on the hub-scoped tables, under a non-owner app role.
- **What it takes:**
  - A SQL-made role `gharbasket_app` (`NOBYPASSRLS`, owns nothing), with DML grants and `ALTER DEFAULT PRIVILEGES`. Staging's and production's `DATABASE_URL` switch to it.
  - `GRANT MAINTAIN` on the two materialized views, for the refresh.
  - The same split locally. Tests run as a non-superuser.
  - An `after_begin` hook that sets `app.hub_ids`, plus a bypass flag for the relay, jobs and consumers, taken from a `contextvar` that `require()` fills.
  - Per-module migrations: a policy `hub_id = ANY (current_setting('app.hub_ids', true)::uuid[]) OR current_setting('app.rls_bypass', true) = 'on'`, with `USING` and `WITH CHECK`.
  - `EXISTS` policies on the child tables, or a `hub_id` copied onto each child.
  - A policy for `gharbasket_ro`.
  - Tests for every table.
- **Effort:** about 7 PRs. One for the role and grants, one for `db.py` and the bypass, four or five migrations grouped by module, and one to switch the connection, with checks. It touches every module, so the module owners review. One more round trip per transaction, about 1 to 2 ms within Singapore. `EXISTS` policies on child tables cost a lookup per row.
- **Value in the pilot:** close to none, with one hub. It does nothing for customer-to-customer access, which is the likelier bug. The bypass flag is a session variable, so it guards against a forgotten `WHERE`, not against SQL injection: injected SQL can call `set_config` too.
- **Risk:** a missed bypass or grant shows up as empty results, not as errors. That means a silent failure on cut-off night, or training jobs that read nothing. Each module's commit pattern has to be checked.

### B. Wait until after the pilot, and enforce the checks in code
Correct the documents now. Keep the checks in code, and add tests that fail when an object route has no check.
- **What it takes:**
  - This PR: the ADR, and corrected handbook 13, 10 and 05, M0 §9 and TDD §4.2.
  - One test PR (`backend/api/tests/security/`):
    1. A **route inventory**. Every route in `app.routes` with a path parameter (about 156 of the 268 route decorators today) is listed in `tests/security/object_routes.py` with how it is scoped: `owner`, `hub`, `society`, `ngo`, `operator`, `public` or `self`. A new route that is not listed fails CI, so its author has to say how it is checked.
    2. A **cross-tenant matrix**. A fixture with two hubs, and two households in different societies. For every listed `GET`, and for each mutation that is safe to repeat, it calls the route as a principal from the other tenant, with a real id from this tenant, and expects 403 or 404. It also checks that the row did not change.
  - The existing grant-level test (`tests/test_permissions.py:183`) stays.
- **Effort:** this docs PR plus one test PR. The test PR is about 300 to 400 hand-written lines, mostly the inventory table and the fixture. It runs in the existing CI job, needs no new infrastructure and costs nothing at run time.
- **Value:** it covers both kinds of leak (hub to hub, and customer to customer) through the API, which is the only way in. It catches a missing check on a **new** route, which is the risk RLS was meant to catch. The documents and the ASVS evidence become true.
- **Limit:** it tests through the routes. A query deep in a service that forgets its `WHERE` is caught only if a route in the matrix reaches it with another tenant's id. RLS would catch that by construction.

### C. A narrower RLS on the personal-data tables only
Put RLS only on `identity.addresses`, `identity.household_members`, `partners.kyc_documents` and `identity.sessions`, keyed on the caller's user or household.
- **What it takes:** the same plumbing as A: the role, the grants, the hook, the bypass, the local superuser split and the `gharbasket_ro` policy. On top of that, `app.user_id` and `app.household_ids` per transaction, and bypasses for the staff paths: KYC review by a hub manager, the admin lookup, erasure and export (M1 §3.3).
- **Effort:** about 5 PRs. The per-table policies are cheap; the plumbing is the cost, and it is the same as in A.
- **Value:** it protects the data that hurts most if it leaks (health tags under DPDP, KYC). But those tables are already reached by very few routes, and every one of them was in the ASVS sample and correct. The KYC numbers are pgcrypto-encrypted, and the files are on R2 behind 5-minute links.

## Decision (proposed)

**Option B for the pilot. The least-privilege app role from option A is set up with the production database (ADR-0036).**

1. **No RLS before or during the pilot.** The handbook and the specs say that RLS is planned and not present, and they point here. Authorisation is the code-level check (`require()` + `Permit.ensure()` for scopes, ownership checks for customers), and that is the control the ASVS review assesses.
2. **The checks are enforced by tests.** Before the pilot, one PR adds the route inventory and the cross-tenant matrix described in option B to `backend/api/tests/security/`. Every new route with a path parameter is then listed in the inventory, with how it is scoped. The M0 §9 test "RLS session variable is set per transaction" is replaced by these two.
3. **The production API connects as a non-owner role.** When the production Neon project is made (ADR-0036 step 5), the owner creates `gharbasket_app` **with SQL**, not in the Console, so it does not inherit `neon_superuser`. It gets `USAGE` on the module schemas; `SELECT, INSERT, UPDATE, DELETE` on their tables; `USAGE, SELECT` on their sequences; `MAINTAIN` on the analytics materialized views; and `ALTER DEFAULT PRIVILEGES` for the tables later migrations make. The prod `DATABASE_URL` uses it. Migrations and backups keep the owner on `DATABASE_URL_DIRECT`. This is a script like `scripts/neon-ro-role.sql`, not app code. It is worth having on its own: a bug or an injection in the API can no longer drop or alter tables, or read through `pg_read_all_data`. It is also the step RLS needs first. Staging may stay on the owner role.
4. **RLS gets its own decision later.** It comes back before a **second hub** goes live, or before franchise owners get console access to more than one hub, whichever is first. That ADR picks between A and C with the role from step 3 already in place. It should cover customer ownership policies, not only `hub_id`.

**Why B and not A or C:**
- With one hub, hub RLS buys nothing in the pilot.
- A and C carry the same plumbing cost, about 5 to 7 PRs across every module. Their failure mode is silent empty reads in the jobs and the relay, in the weeks the team most needs them to work.
- B's tests cover the likelier leak, customer to customer, which hub RLS does not.
- B can be done before the pilot. A could not be done safely in that time.

## Alternatives considered
- **Option A now:** rejected for the pilot (see above). It returns as the likely shape of the later ADR.
- **Option C now:** rejected for the pilot. It needs all of A's plumbing to protect tables that few routes reach, all of which were checked.
- **Leave the documents as they are, and build RLS "soon":** rejected. The handbook would go on claiming a control that does not exist, and the ASVS evidence would be false.
- **Per-hub databases or schemas:** rejected. Nothing at pilot scale calls for it, and it would break the one-schema-per-module rule (handbook 05).

## Consequences
- (+) The documents match the code. ASVS V8 can be judged on the control that exists.
- (+) A new object route without a check fails CI, and that covers customers as well as hubs.
- (+) Production's API cannot change the schema or read beyond its grants, and the role RLS needs already exists.
- (−) A query that forgets its tenant filter, on a path no route in the matrix reaches, is not caught. RLS would catch it.
- (−) The inventory must be kept up to date. Every route PR touches `tests/security/object_routes.py`.
- (−) The owner has one more setup step for the production database. `REFRESH MATERIALIZED VIEW` needs `MAINTAIN` (Postgres 17+, available on Neon's Postgres 18) or the refresh fails.
- (−) `modules/identity/permissions.py:3` ("once row-level security arrives") is code, so this docs PR leaves it alone. It is reworded in the test PR (step 2) to point here.

## Order of work (each step a PR the owner approves)
1. This ADR and the corrected documents (docs only).
2. `tests/security/`: the route inventory and the cross-tenant matrix, with a two-hub fixture. The `permissions.py:3` comment is reworded. Backend, reviewed as usual.
3. With ADR-0036 step 5: `scripts/neon-app-role.sql` and `.sh`, in the same pattern as `neon-ro-role.*`. The script makes `gharbasket_app`, grants its rights, checks that it cannot `CREATE` in a module schema, read `pg_authid` or bypass RLS, and writes `DATABASE_URL` for prod in Infisical without printing it. Handbook 12 gets the step.
4. Later, before a second hub: a new ADR for RLS (A or C) on top of step 3.

## What the owner must do
- **Now:** review and accept or reject this ADR.
- **To confirm one assumption (read-only, staging):** `infisical run --env=staging -- sh -c 'psql "$DATABASE_URL" -Atc "select current_user, r.rolbypassrls, pg_has_role(current_user, '\''neon_superuser'\'', '\''member'\'') from pg_roles r where r.rolname = current_user"'`. If it prints the owner, `t` and `t`, the API runs as an owner that bypasses RLS, as this ADR assumes.
- **With the production project (step 3):** run the role script against the prod direct URL, and put the prod `DATABASE_URL` on the new role. Do **not** create `gharbasket_app` in the Neon Console: a Console role is a `neon_superuser` member and bypasses RLS.

## Related
[ADR-0006](0006-postgresql-centric-data-platform.md) · [ADR-0021](0021-jobs-without-a-broker.md) · [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) · [ADR-0036](0036-production-environment-for-the-pilot.md) · Handbook [13 §13.4](../handbook/13-security-and-compliance.md) · Handbook [05 §5.9](../handbook/05-data-architecture.md) · [M0 §9](../modules/M0-platform.md)
