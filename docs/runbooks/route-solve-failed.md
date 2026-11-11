# Runbook: a route plan failed or is waiting for review

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

**The ML service is never the reason a plan fails.** When it is asleep or down, M7 plans the routes itself in under a second
(M7 §3.2): the stop order of the hub's last published plan in the past 7 days (`fallback_previous`), else nearest neighbour
from the hub (`fallback_nearest`). Tiffin plans never use ML (`nearest`, M7 §3.7). So a `failed` plan is a data problem
at the hub, and a plan left `solved` is one M7's own check did not like. Both have to be sorted before the vans leave at
06:30 (box) or before the 11:30 tiffin pickups.

## Symptoms

- **healthchecks `ops`** from `ops_check`:
  - at 23:45: `logistics.plans_failed: box plan for <tomorrow> at hub <id> failed: <error>`;
  - from 06:30: `logistics.plans_unreviewed: box plan for <today> at hub <id> is waiting for review; see /routes`.
  - A failed tiffin plan (09:35) shows in the 12:30 run.
- **Hub managers and admins get a message** "Routes for <hub> need a look" (`logistics.plan_needs_review`), linking to the
  console's Routes page. It waits for quiet hours, so a plan left at 23:15 reaches phones at 07:00; the 06:30 `ops`
  alert comes first.
- **Console → Routes** shows "Solved, not published", or a failed plan with its error. Riders see no route.

## Checks

1. **Open Routes for the hub and date.** The plan's `status`, `source`, `error` and `summary.problems` say which case
   this is. The API behind it is `GET /v1/hubs/{id}/route-plans?date=&kind=` (newest first) and `GET /v1/route-plans/{id}`.
2. **Did the job run?** `infisical run --env=staging -- just job-runs solve_routes` (or `solve_tiffin_routes`). Its
   summary counts `published`, `solved`, `failed`, `skipped` and `abandoned` plans. No run at all: see
   [`jobs-scheduler-down.md`](jobs-scheduler-down.md) and run it by hand (README).
3. **Was the cut-off on time?** A plan solved before late orders were locked is missing them. See
   [`cutoff-failed.md`](cutoff-failed.md) step 4.

## Common causes and fixes

| Status and message | Cause | Fix |
|---|---|---|
| `failed`: `no active cargo_3w vehicle at the hub` (box) or `no active two_wheeler vehicle at the hub` (tiffin) | No vehicle of that kind is active | Console → Vehicles: activate one (or add it), then solve again |
| `failed`: `no orders with a location` | None of the day's societies has a gate or a point inside its boundary | Console → Societies: set the gate (or the boundary) of the societies with orders, then solve again |
| `failed`: `abandoned: not solved within 10 minutes` | A manual solve was lost (the API restarted during it) | Solve again; nothing else is needed |
| `failed`: `unexpected error: <Type>` | A bug; the API logs have `route_solve_failed` with the plan id | Solve again once; if it fails the same way, use the previous day's plan (below) and open a `fix/` branch |
| `solved`, `summary.problems` lists a society with no location | That society was left off the plan | Set its location, then solve again |
| `solved`, problems such as `van 2: over capacity leaving the hub` or a late return | The fleet is too small for the day, or a window cannot be met | Add or activate a vehicle and solve again, or publish with the problems accepted if the hub manager says the van can do it |
| `solved`, `plan_incomplete` on publish | An order's stop is on no route, or on two | Never publishable; fix the cause (vehicles, locations) and solve again |

## Recovery steps

1. **Fix the data first** (vehicles, society locations), as the table says.
2. **Solve again from the console:** Routes → Solve again (or Solve now). This is `POST /v1/hubs/{id}/route-plans` with
   `{ "date", "kind" }`, for today or tomorrow; it answers 202 and solves in the background, and a second solve while
   one runs gets 409 `logistics.solve_running`. A manual plan is never published by itself.
3. **Publish it.** With no problems, Publish. With problems the hub manager has accepted, publish with them accepted
   (`accept_problems: true`); the API records who did it and the problems in the audit log. Publishing replaces the
   date's published plan and cancels its routes, so riders and M6's packing sequence follow the new one.
4. **Riders.** A route takes its vehicle's default rider. Set or change riders on the published routes (Routes → the route
   → rider); each rider is told when the route is published.
5. **If the data cannot be fixed before dispatch** (no vehicle at all, for example), there is no way to enter routes by
   hand in the console. Riders cannot record handovers without a published route, so tell the hub manager by phone at
   once. This is the case to prevent: the 23:45 alert leaves the night to fix vehicles and locations.
6. **Bring the alert back:** run `ops_check` by hand (`gh workflow run jobs-staging.yml -f job=ops_check`).

## What not to do

- Do not re-solve after a route has started: the API refuses it (409 `logistics.routes_started`) because riders and
  packing already follow the plan. Change riders or skip stops on the routes instead.
- Do not accept problems you have not read with the hub manager: an over-capacity van means bags that do not fit.
- Do not wait for the ML service: it is not needed for a plan, and the fallback is the designed path (handbook 08 §8.2).

## Related

M7 §3.2 (solve, fallback, publish), §3.7 (tiffin routes) and §6 (jobs), M10 §3.4, ADR-0033, handbook 12 §12.6 ("route
solve < 30 s per slot"), [`ml-service-down.md`](ml-service-down.md), [`README.md`](README.md).
