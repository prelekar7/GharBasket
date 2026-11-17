# infra/k6: API load tests

k6 scripts for the API. They are written for a **local** stack. A run against staging needs the owner's go first:
read [WARNING: staging](#warning-staging) before you point them anywhere else.

The test plan sets two load targets (handbook 11 §11.6, M4 §9): "cut-off with 2,000 orders" and "200 concurrent box
edits". The thresholds are the pilot SLOs in handbook 12 §12.6:

| What | Threshold (k6) |
|---|---|
| Reads (`kind:read`, `kind:public`) | `http_req_duration` p95 < 300 ms |
| Writes (`kind:write`: box edit, approve) | `http_req_duration` p95 < 800 ms |
| Errors, per kind | `http_req_failed` < 1 % (the docs give no error target) |
| Checks | > 99 % pass |
| Cut-off lock | < 60 s, and < 60 s scaled to 2,000 orders |

Setup's sign-ins are tagged `kind:setup` and are not held to these thresholds.

| Script | What it does | Endpoints |
|---|---|---|
| `public.js` | Pages anyone can open without signing in, plus one request in ten for a society that does not exist (404, never cached) | `GET /v1/public/societies/{slug}`, `GET /v1/public/cooks/{handle}` (only with `GB_COOK_HANDLES`) |
| `customer-reads.js` | One app session: app start, the Week screen, the Wallet, this month's statement, with pauses between screens (about 9 requests) | `/v1/me`, `/v1/households/me`, `/v1/cycles/current`, `/v1/orders?status=payment_failed`, `/v1/budget-lock`, `/v1/wallet`, `/v1/wallet/entries`, `/v1/statements?period=` |
| `box-writes.js` | "200 concurrent box edits": each VU is one household. It reads its box, changes one line with `If-Match`, and approves the new version with an `Idempotency-Key` and `If-Match`. One approval in ten is sent again with the same key and must come back replayed. Every second edit puts the line back | `GET /v1/cycles/{id}`, `PATCH /v1/cycles/{id}/lines`, `POST /v1/cycles/{id}/approve` |
| `cutoff-burst.js` | The evening before delivery, squeezed into about 7 minutes: a bump after the 19:00 reminder, a quiet spell, a climb to a peak just before 21:00, then a hard stop at the cut-off. App sessions and box edits run together. The docs give the timing, not the numbers, so the peaks are assumptions (`GB_READ_PEAK`, `GB_WRITE_PEAK`) | both of the above |
| `cutoff-lock.js` | Triggers `cutoff_lock` once, with the boxes' own cut-off as `scheduled_for`, and times it. **Local only, and one way** (it needs `GB_CONFIRM_CUTOFF=yes`) | `POST /v1/internal/jobs/cutoff_lock` |

Each script has three profiles. `smoke` is a few requests to check the script works. `load` is the default for a local
API. `staging` is small, short and stops itself early, and it is forced for any host other than localhost.

## Run locally

```bash
brew install k6                                   # once
just up && just migrate && just seed              # Postgres, Valkey, the seed
just dev                                          # APP_ENV=dev: dev_phone sign-in works
just load public -e GB_PROFILE=smoke              # = k6 run -e GB_PROFILE=smoke infra/k6/public.js
just load customer-reads
```

`public.js` and `customer-reads.js` work on the seed alone. The write scripts need open boxes (next section).

**Sign-in.** Setup signs customers in with `dev_phone`. That works only when the API runs with `APP_ENV=dev`
(handbook 13 §13.3 item 8). The customers are the seed's (+91 98000 01001–01004 and 01012), then the simulator's
households (+91 98001 00001 onwards, `GB_SIM_USERS` of them). You can also list phones yourself in `GB_PHONES`. A phone
without an account becomes a new, empty customer in your local database. Access tokens last 15 minutes and the scripts
do not refresh them, so keep a run under about 14 minutes. Teardown signs the sessions out.

**Rate limits, and `GB_IP_HEADER`.** Locally, every request comes from 127.0.0.1. The API's per-address limits would
then stop the test long before the server is busy: 30 sign-ins per 10 minutes, 10 per phone, and 60 public pages a
minute. To give each user and each VU its own address in 198.18.0.0/15 (the benchmark range of RFC 2544), start the API
with:

```bash
CLIENT_IP_SOURCE=header:x-gb-loadtest-ip just dev
just load box-writes -e GB_IP_HEADER=x-gb-loadtest-ip
```

Without the header, `public.js` stays at 50 requests a minute and the sign-in scripts manage about 30 users per 10
minutes. The scripts ignore `GB_IP_HEADER` for any host other than localhost.

For numbers closer to a deployed API, run without `--reload`, as Render does:
`APP_ENV=dev CLIENT_IP_SOURCE=header:x-gb-loadtest-ip uv run uvicorn gharbasket.main:create_app --factory --port 8000 --no-proxy-headers --no-access-log`.

### Local data for the write scripts

The write scripts need households whose current box is `awaiting_approval` (or `approved`) and whose cut-off is still
ahead. `box-writes.js` needs at least 2 minutes of that left, `cutoff-burst.js` at least 10. The seed societies deliver
on Tuesdays and the cut-off is 21:00 IST on the Monday. Take T = the next Tuesday whose Monday 21:00 has not passed
yet, and run the two evening jobs for T at their T-3 times:

```bash
just sim --weeks 2 --write-db --adoption 0.25     # about 215 subscribed households (864 flats in the hub's societies)
# T = Tue 17 Nov 2026: T-3 is Sat 14 Nov, and the boxes stay open until Mon 16 Nov 21:00 IST
just job generate_weekly_plans  --scheduled-for 2026-11-14T18:00:00+05:30
just job send_box_ready_notices --scheduled-for 2026-11-14T18:30:00+05:30
```

The seed's one box slot takes 120 orders. Beyond that, an approval answers `409 orders.slot_full`, which the scripts
count as a failure (`gb_refusals`). For more than 120 households, raise the capacity first as the hub manager. The slot
id is printed by `just seed` ("delivery: slot …") and is in every box (`slot.id`):

```bash
TOKEN=$(curl -s localhost:8000/v1/auth/exchange -H 'Content-Type: application/json' \
  -d '{"dev_phone":"+919800001008","device":{"platform":"android"}}' | jq -r .access_token)
curl -s -X PATCH localhost:8000/v1/admin/slots/<slot id> -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"capacity": 2000}'
```

Then, for example:

```bash
just load box-writes -e GB_PROFILE=smoke
just load box-writes -e GB_IP_HEADER=x-gb-loadtest-ip -e GB_USERS=205 -e GB_VUS=200
just load cutoff-burst -e GB_IP_HEADER=x-gb-loadtest-ip -e GB_USERS=205
just load cutoff-lock -e GB_CONFIRM_CUTOFF=yes    # last: locks and debits that delivery date's approved boxes
```

Setup prints how many current boxes it found in each status. If they are all `draft`, run `send_box_ready_notices`.
If you get `404 subscriptions.no_cycle`, run `generate_weekly_plans` for the right T.

`cutoff-lock.js` locks only boxes that are approved, so run `box-writes.js` or `cutoff-burst.js` first. The seed has
at most a few hundred households, so the script also reports the time scaled to 2,000 orders (`gb_cutoff_ms_per_2000`,
a linear estimate). After the lock, those boxes cannot be edited any more. Run the two jobs for the next T, or
`just reset` and start again.

### Settings (`-e NAME=value`)

| Name | Default | What |
|---|---|---|
| `GB_BASE_URL` | `http://localhost:8000` | The API. Any other host needs `GB_STAGING_GO=yes` and gets the `staging` profile |
| `GB_PROFILE` | `load` | `smoke`, `load` (ignored off localhost) |
| `GB_IP_HEADER` | unset | Locally: the header the API reads the caller's address from (see above) |
| `GB_PHONES` | seed + simulator | Phones to sign in, comma-separated |
| `GB_USERS`, `GB_SIM_USERS` | per script | How many customers to sign in, and how many of them come from the simulator |
| `GB_VUS` | 200 (`box-writes`) | Concurrent households editing |
| `GB_SESSIONS_PER_MIN` | 600 | `customer-reads` sessions a minute at the plateau |
| `GB_RATE_PER_MIN` | 50, or 3,000 with `GB_IP_HEADER` | `public` requests a minute |
| `GB_READ_PEAK`, `GB_WRITE_PEAK` | 900, 600 | `cutoff-burst` iterations a minute at 20:50–21:00 |
| `GB_DURATION` | per script | The plateau (`load` profile) |
| `GB_SOCIETY_SLUGS`, `GB_COOK_HANDLES` | the seed's societies, none | Public pages to ask for |
| `GB_CONFIRM_CUTOFF` | unset | `yes` lets `cutoff-lock` run |
| `GB_CUTOFF_AT` | the boxes' `cutoff_at` | `scheduled_for` of the cut-off run |
| `GB_JOBS_TOKEN` | unset | Only if your local `.env` sets `JOBS_TOKEN` |
| `GB_ACCESS_TOKENS` | unset | Remote runs only: pre-made access tokens, comma-separated |
| `GB_STAGING_GO`, `GB_ALLOW_REMOTE_WRITES` | unset | See below |

Secrets (`GB_ACCESS_TOKENS`, `GB_JOBS_TOKEN`) come from the environment only. Never write them into a script, a
justfile line or a committed file.

Custom metrics: `gb_version_conflicts` (share of writes answered 412: two VUs on one box), `gb_idempotent_replays`,
`gb_refusals` (by `code`), `gb_rate_limited` (429s on public pages), `gb_cutoff_ms`, `gb_cutoff_ms_per_2000`,
`gb_cutoff_orders`.

## WARNING: staging

**Do not run these scripts against staging, or any shared host, without the owner's (Prathamesh Relekar's) explicit go
for that run.** Staging is a set of free plans, and a load test can take down all of them, not only the API:

- **Cloudflare.** On 14 Nov 2026, during the client-IP checks of handbook 13 §13.3, Cloudflare's DDoS protection
  challenged our own IP and Vercel's after a few hundred requests a minute. A challenge is a 403 or an HTML page, not
  our API. Once it starts, the PWA's own traffic through Vercel can be challenged too, so real users are blocked.
- **Render** runs the API on the free plan: one small instance, asleep after 15 idle minutes. Requests beyond what it
  can take queue up and time out. That shows the free plan's limits, not the pilot SLOs.
- **Neon** gives the project 100 CU-hours a month (`NEON_CU_HOURS_LIMIT`, `scripts/neon_usage_check.py`). Traffic keeps
  the compute awake and spends them. When they run out, the compute is suspended until the next month and staging is
  down, scheduled jobs included. Neon is one database for both the `dev` and `staging` environments.
- **Per-address limits** apply on staging too: 60 public pages a minute, 30 sign-ins per 10 minutes. `GB_IP_HEADER` is
  ignored there, because the address comes from Cloudflare.

A staging run needs all of the following:

1. **The owner's go**, for a stated time window. Check Neon's remaining hours first (`infisical run --env=staging -- uv
   run python scripts/neon_usage_check.py`).
2. **A Cloudflare WAF skip rule for the tester's IP**, added before the run and removed after it.
3. **Low VUs and a short run.** Any host other than localhost gets the `staging` profile: at most 5 VUs, 30 to 60
   requests a minute, 1 to 2 minutes. It aborts once more than 5 % of requests fail. Do not raise it with
   `GB_RATE_PER_MIN` or similar without the owner agreeing.
4. **Pre-made access tokens** in `GB_ACCESS_TOKENS`, from the environment only. There is no `dev_phone` on staging. Sign
   a test account in (Firebase test number, `POST /v1/auth/exchange`) and pass its access token. It lasts 15 minutes.
5. **Reads only.** `box-writes.js` and `cutoff-burst.js` refuse a remote host unless `GB_ALLOW_REMOTE_WRITES=yes`,
   because they make real orders in the shared database. Use that only with the owner's go for writes, and only on test
   accounts. `cutoff-lock.js` never runs remotely, since the cut-off on staging belongs to the VM scheduler.

```bash
# only after steps 1 to 4
GB_ACCESS_TOKENS=… k6 run -e GB_BASE_URL=https://api.gharbasket.co.in -e GB_STAGING_GO=yes infra/k6/customer-reads.js
```
