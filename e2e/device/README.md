# e2e/device

A delivery day that happens **today**, on your Mac, for testing the Android app's field roles on a real phone: the
packer (scan bag QR codes), the captain (handover, delivery photo), the rider (start the route, background GPS, kitchen
pickup scan, door delivery with a photo) and the customer (live tracking). Owners: Rhythm Thakur and Manish Raje.

```bash
just up && just up-storage                       # Postgres, Valkey, SeaweedFS (S3 on :8333)
just device-day                                  # builds the day (about a minute), then serves the API
adb reverse tcp:8000 tcp:8000                    # the phone's localhost:8000 is the Mac's API
adb reverse tcp:8333 tcp:8333                    # photo uploads go to SeaweedFS, also on localhost
just device-day pack --leave 3                   # (second terminal) pack the queue, leave 3 bags for the phone
```

`just device-day` prints the summary when the day is ready (and keeps it on screen above the server's log):
today's date T, one line per role with its phone, the rider's routes with every stop and who works it, how many packing
tasks and bag codes exist, a few `GB1:<32 hex>` bag codes and the kitchen's `GB1A:<hex>` pickup code, the exact `adb
reverse` commands and the JOBS token. All codes are also in `e2e/device/out/codes.txt` (git-ignored), one per line after
a `#` comment naming the bag, to show on a screen as QR codes (for example `qrencode -t ansiutf8 'GB1:...'` after
`brew install qrencode`, or any QR page).

## What it builds

A database of its own, `gharbasket_device` (dropped and rebuilt on every run), migrated to head and seeded with
`tools/seed`, plus about 26 simulated households (`gharbasket_sim`'s `populate` and `subscribe_all`, as `just sim
--write-db` does) and two seed customers (Priya, Ramesh). Then delivery day **T = today (IST)**, on the real clock: the
seed societies deliver on today's weekday (through the admin API, as `e2e/web/fixtures/delivery-day.js` does) and the
jobs run in the order and at the IST times of `infra/jobs/schedule.yaml`, each through `POST /v1/internal/jobs/{name}`
with `scheduled_for`:

| T-relative time | What | Done as |
|---|---|---|
| T-4 10:00 | simulated households and Priya, Ramesh subscribe | simulator services; the two seed customers through the API |
| T-3 18:00, 18:30 | `generate_weekly_plans`, `send_box_ready_notices` | jobs |
| T-3 10:00 to 11:00 | Lata (01005, the seed's kitchen partner) becomes a verified home kitchen with a pin, skills, capacity, an approved lunch menu | seed-style write for the partner and kitchen rows (as `tools/seed` does the captains); the menu is a row, as in the e2e tiffin journey (M2 has no menu write); the rest through the API as Lata, the hub manager and Priya |
| T-2 10:00 | every household approves its box (If-Match) and tops up its wallet; Ramesh asks for auto-approve; Priya books 2 tiffin portions | customer endpoints as each household (dev sign-in), dev Razorpay orders |
| T-1 19:00, 20:55, 21:00 | `remind_unapproved`, `apply_auto_approve`, `cutoff_lock` | jobs (wallets are funded, so every charge succeeds) |
| T-1 21:10 | `build_procurement_plan`; the manager makes the purchase orders and sends them | job, then the hub manager's endpoints |
| T-1 21:30 to 22:00 | `assign_kitchens`; Lata accepts; `assignment_acceptance_timeout` | jobs, Lata's endpoint |
| T-1 23:00 to 23:15 | `payment_grace_expiry`, `refresh_procurement_plan`; the manager registers the van (Arjun as its default rider); `solve_routes` | jobs, the manager's endpoint |
| T 04:00 | the packer receives the goods | packer's endpoint |
| T 05:00 | `build_packing_tasks` | job |
| T 06:00 to 10:30 | Lata starts cooking; `refund_unassigned_tiffins`; the manager registers a two-wheeler; `solve_tiffin_routes`; Lata marks the tiffins ready (the pickup code appears) | jobs, the manager's and Lata's endpoints |

It stops there, at the start of the morning: packing tasks are **queued** (nobody has packed), the rider (01006) has
**published, unstarted** routes for today (the cargo-van box route and the lunch tiffin route), and the box route has a
stop in five societies. Two of them have a captain (Sunrise Towers: Meena 01004; Krishna Kunj: Savita 01012), so the
rider hands over to her and she delivers the doors; the rest have none, so the rider switches those stops to door
delivery when he starts the route. Arjun can have only one route in progress: run the morning box route and complete it, then the lunch tiffin run.
The route cannot start with unpacked bags: pack some on the phone as the packer, then
`just device-day pack` for the rest (or `pack --leave N`). `pack` only talks to this tool's own server (it
writes `out/server.json` while it serves) and refuses any other API on the port.

Then it serves the real app on `127.0.0.1:8000` (`DEVICE_HOST=0.0.0.0` for a phone on the Wi-Fi; dev sign-in has no
code, so it stays on loopback by default and prints a warning otherwise). It will not start while the port is busy:
stop `just dev` or `just dev-real`, or set `DEVICE_PORT`. It serves with:

- `app_env=dev`; `firebase_project_id=gharbasket-2f523` (the phone's Firebase test numbers are verified as real ID
  tokens; dev sign-in with `{"dev_phone": ...}` also works);
- `cors_allow_origins` `https://localhost` and `http://localhost` (the Capacitor WebView);
- S3 on `http://localhost:8333` (SeaweedFS), bucket `gb-private`, so presigned links work from the phone;
- no FCM, no Razorpay (top-ups are dev orders), no ML (the rule-based fallbacks answer), no WhatsApp (even when
  run under `infisical run`, whose environment would otherwise switch it on);
- the JOBS token (random per run, printed; `DEVICE_JOBS_TOKEN` fixes it) for the operator endpoints, and a small
  ticker that triggers `persist_gps_pings`, `outbox_sweep` and `send_deliveries` once a minute (what the crons do on a
  delivery morning), so GPS pings reach Postgres and the customer's messages appear without you running anything.

The day is good until midnight IST: a route starts only on its own date.

## Which steps act as which role

Everything goes through HTTP as the person who does it in life: **admin** (01011) moves the societies' delivery weekday;
**customers** (their own dev sign-in) subscribe, approve, top up, book a tiffin; **hub manager** (01008) makes and sends
the purchase orders, registers vehicles, approves the menu, sets a route's rider if the van's default did not; **packer**
(01007) receives the goods and, with `pack`, packs; **Lata** (01005) accepts, starts and readies the tiffins; **cron** is
the operator token. The tool never writes a row of another module's table except the two stand-ins named above.

## Which jobs needed special handling, and why

All jobs honour `scheduled_for` for the *day they work on* (generate, notices, reminders, auto-approve, cut-off, grace,
procurement, kitchens, routes, packing), so none needed the clock moved. What does read the clock is the **deadline
checks in the services under them**: a customer approving, `apply_auto_approve` (it approves through the same rule), a
tiffin booking, a kitchen accepting, a menu approval, each refuse after T-1 21:00 / 22:30, which is in the past by
definition when T is today; and `send_box_ready_notices` stamps the notice with the clock, and auto-approve needs the notice
24 h before the cut-off. For those steps the build sets the four module clocks the services read
(`subscriptions.service.now`, `orders.service.now`, `partners.kitchens.now`, `catalog.tiffin_review.now`: the first two are
what the simulator's `simulated_time` sets) to the step's time, then puts them back before the server starts. No
`time_machine`, nothing in the database, and logistics, hub operations, packing, receiving, the wallet and sign-in never
leave the real clock. The API the phone talks to has no patched clock at all.

## Safety

- The database is `gharbasket_device`, **exactly** (`assert_device_database`; checked first in every command and again
  inside the drop). `gharbasket`, `gharbasket_test`, `gharbasket_e2e` and `gharbasket_e2e_web*` are refused, as is any
  non-local host. Valkey must be the local db 12 (flushed; the other suites use 0, 13, 14, 15 and 16-127).
- No secret is stored: the Firebase project id is public, the S3 keys are SeaweedFS's local defaults, the JOBS token is
  generated per run.
- Tests: `uv run --directory e2e/backend pytest ../device` (the guard, the settings, the clock patch); with
  `DEVICE_DAY_RUN=1` it also builds the whole day once.

## Settings

`DEVICE_DATABASE_URL` (must name `gharbasket_device`), `DEVICE_VALKEY_URL` (db 12), `DEVICE_HOST`, `DEVICE_PORT`,
`DEVICE_S3_URL` / `_ACCESS_KEY` / `_SECRET_KEY` / `_BUCKET`, `DEVICE_FIREBASE_PROJECT`, `DEVICE_JOBS_TOKEN`,
`DEVICE_ADOPTION` (share of flats that subscribe, default 0.03), `DEVICE_SIM_SEED`, `DEVICE_TIFFIN=0` (skip the kitchen).

Commands: `run` (default: build, then serve), `prepare` (build only), `serve` (serve what is already built), `pack
[--leave N]` (against the running server).
