"use client";

import {
  Badge,
  Button,
  EmptyState,
  RouteStopCard,
  SegmentedControl,
  Select,
  toast,
} from "@gharbasket/ui";
import { MapPanel } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { formatClock, formatDay, formatTime, newKey, plural, rupees } from "@/lib/fleet.js";
import "@/lib/maps.js";
import { liveSnapshot, planStatus, publishPlan, solvePlan } from "./actions.js";

const POLL_MS = 15_000;
const SOLVE_POLL_MS = 3_000;
const LEGEND = [
  { kind: /** @type {const} */ ("hub"), label: "Hub" },
  { kind: /** @type {const} */ ("stop"), label: "Stop" },
  { kind: /** @type {const} */ ("van"), label: "Van now" },
  { kind: /** @type {const} */ ("route"), label: "Planned route" },
];
const DAYS = [
  { value: "today", label: "Today" },
  { value: "tomorrow", label: "Tomorrow" },
];

/** @param {{ stops: { seq: number, status: string, eta_now: string | null }[] } | null} live @param {number} seq */
const liveStopOf = (live, seq) => live?.stops.find((s) => s.seq === seq);

/** What a stop is called when it has no society (a food bank or a kitchen pickup). @param {import("./data.js").RouteStop} s */
const stopName = (s) =>
  s.society?.name ??
  (s.kind === "bank" ? "Food bank drop" : s.kind === "kitchen" ? "Kitchen pickup" : "Stop");

/** @param {import("./data.js").RouteStop} s @param {string | null} etaNow */
function windowText(s, etaNow) {
  if (s.handed_over_at) return `handed over ${formatTime(s.handed_over_at)}`;
  if (s.status === "skipped") return "skipped";
  return `ETA ${formatClock(etaNow ?? s.eta)}`;
}

/** The card's status: the first stop still to do is the one the van is heading for. @param {import("./data.js").RouteStop[]} stops @param {boolean} running */
function cardStatuses(stops, running) {
  let current = -1;
  if (running) current = stops.findIndex((s) => s.status === "pending" || s.status === "arrived");
  return stops.map((s, i) =>
    s.status === "handed_over"
      ? /** @type {const} */ ("done")
      : s.status === "skipped"
        ? /** @type {const} */ ("issue")
        : s.status === "arrived" || i === current
          ? /** @type {const} */ ("current")
          : /** @type {const} */ ("todo"),
  );
}

/**
 * The live map and stop list of one day's routes, the way the rider and the captains see it, and the day's plan.
 * @param {{ date: string, day: import("./data.js").Day, routes: import("./data.js").RouteRecord[],
 *   plan: import("./data.js").RoutePlan | null, hub: { name: string, lat: number | null, lng: number | null },
 *   capacity: Record<string, number>, canPlan: boolean }} props
 */
export function RoutesView({ date, day, routes, plan, hub, capacity, canPlan }) {
  const [pending, startTransition] = useTransition();
  const [, setDay] = useQueryState(
    "day",
    parseAsStringLiteral(["today", "tomorrow"])
      .withDefault("today")
      .withOptions({ shallow: false, startTransition, clearOnDefault: true }),
  );
  const [picked, setPicked] = useState(routes[0]?.id ?? "");
  const route = routes.find((r) => r.id === picked) ?? routes[0];

  // The van moves: ask for its position every 15 seconds while the route is out, and keep the last one if a call fails.
  const [live, setLive] = useState(
    /** @type {{ position: import("./data.js").VanPosition | null, stops: { seq: number, status: string, eta_now: string | null }[] } | null} */ (
      null
    ),
  );
  const [prev, setPrev] = useState(route?.id);
  if (route?.id !== prev) {
    setPrev(route?.id);
    setLive(null);
  }
  const routeId = route?.id;
  const running = route?.status === "in_progress";
  useEffect(() => {
    if (!routeId || !running) return;
    let stopped = false;
    const read = async () => {
      if (document.hidden) return;
      const next = await liveSnapshot(routeId);
      if (next && !stopped) setLive(next);
    };
    read();
    const timer = setInterval(read, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [routeId, running]);

  const van = live?.position ?? null;
  const riderName = route?.rider?.name ?? "No rider yet";

  const markers = useMemo(() => {
    if (!route) return [];
    return [
      ...(hub.lat !== null && hub.lng !== null
        ? [
            {
              id: "hub",
              kind: /** @type {const} */ ("hub"),
              label: hub.name,
              lat: hub.lat,
              lng: hub.lng,
            },
          ]
        : []),
      ...route.stops.flatMap((s) =>
        s.society
          ? [
              {
                id: `stop-${s.seq}`,
                kind: /** @type {const} */ ("stop"),
                n: s.seq,
                label: `${stopName(s)} · ${
                  s.handed_over_at
                    ? `done ${formatTime(s.handed_over_at)}`
                    : `ETA ${formatClock(liveStopOf(live, s.seq)?.eta_now ?? s.eta_now ?? s.eta)}`
                }`,
                lat: s.society.location.lat,
                lng: s.society.location.lng,
              },
            ]
          : [],
      ),
      ...(van
        ? [
            {
              id: "van",
              kind: /** @type {const} */ ("van"),
              label: `${riderName.split(" ")[0]} · ${formatTime(van.at)}`,
              lat: van.lat,
              lng: van.lng,
            },
          ]
        : []),
    ];
  }, [route, van, hub, riderName, live]);
  const line = useMemo(() => {
    if (!route) return [];
    const ids = route.stops.filter((s) => s.society).map((s) => `stop-${s.seq}`);
    return hub.lat !== null ? ["hub", ...ids, "hub"] : ids;
  }, [route, hub]);
  const statuses = route ? cardStatuses(route.stops, running) : [];

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            Routes · {formatDay(date)}
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {plan?.published_at
              ? `Published at ${formatTime(plan.published_at)} by the route solver. The van’s position updates every 15 seconds while it’s out.`
              : "The route solver plans the day’s routes from the locked boxes, and a hub manager publishes them for the riders."}
          </p>
        </div>
        <SegmentedControl
          label="Day"
          options={DAYS}
          value={day}
          onChange={(v) => setDay(v === "tomorrow" ? "tomorrow" : "today")}
        />
      </div>

      <div
        className={`flex flex-col gap-4 transition-opacity motion-reduce:transition-none ${pending ? "opacity-60" : ""}`}
        aria-busy={pending}
      >
        {!route ? (
          <div className="rounded-md bg-surface p-4">
            <EmptyState
              title={`No route for ${day === "today" ? "today" : "tomorrow"} yet`}
              body="The solver plans the routes at 23:15 the night before, from the locked boxes. A hub manager can also solve a plan now."
            />
          </div>
        ) : (
          <div className="grid items-start gap-4 min-[900px]:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
            <div className="flex min-w-0 flex-col gap-1">
              <MapPanel
                label={`Route ${routes.indexOf(route) + 1} on the map`}
                height={480}
                markers={markers}
                route={line}
                legend={LEGEND}
              />
              <p className="sr-only" aria-live="polite">
                {van ? `${riderName}’s van, last seen ${formatTime(van.at)}` : ""}
              </p>
            </div>
            <div className="flex min-w-0 flex-col gap-3">
              {routes.length > 1 && (
                <Select
                  label="Route"
                  value={route.id}
                  onChange={setPicked}
                  options={routes.map((r, i) => ({
                    value: r.id,
                    label: `Route ${i + 1} · ${r.rider?.name ?? "No rider yet"}`,
                  }))}
                />
              )}
              <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
                <h2 className="text-[17px] leading-6 font-bold">
                  Route {routes.indexOf(route) + 1} · {riderName} · {route.vehicle.label}
                </h2>
                <p className="-mt-2 text-sm leading-5 text-fg-secondary">
                  Leaves {formatClock(route.depart)}, back by {formatClock(route.return_by)}
                  {route.status === "draft" ? " · not published yet" : ""}
                </p>
                {route.stops.map((s, i) => (
                  <RouteStopCard
                    key={s.id}
                    n={s.seq}
                    society={stopName(s)}
                    where={s.slot?.label ?? ""}
                    captain={
                      statuses[i] === "current"
                        ? undefined
                        : s.rider_mode
                          ? "The rider hands over"
                          : (s.captain?.display_name ?? undefined)
                    }
                    window={windowText(s, liveStopOf(live, s.seq)?.eta_now ?? s.eta_now)}
                    crates={s.crates}
                    bags={s.bags}
                    status={statuses[i]}
                    onNavigate={() => window.open(s.navigate_url, "_blank", "noopener")}
                  />
                ))}
              </section>
            </div>
          </div>
        )}
        <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
          <SolverPanel plan={plan} route={route} capacity={capacity} />
          <PlanPanel plan={plan} date={date} canPlan={canPlan} />
        </div>
      </div>
    </>
  );
}

const SOURCE_NOTE = /** @type {const} */ ({
  fallback_previous: "The solver was unavailable, so this is the last published stop order.",
  fallback_nearest: "The solver was unavailable, so stops are ordered nearest first.",
});

/**
 * What the solve produced: the plan's summary, and the load of the route being looked at.
 * @param {{ plan: import("./data.js").RoutePlan | null, route: import("./data.js").RouteRecord | undefined, capacity: Record<string, number> }} props
 */
function SolverPanel({ plan, route, capacity }) {
  const summary = plan?.summary;
  const rows = [
    summary ? ["Distance", `${summary.km.toFixed(1)} km`] : null,
    summary ? ["On time", `${Math.round(summary.on_time * 100)}%`] : null,
    summary ? ["Cost", rupees(summary.cost_paise)] : null,
    route
      ? [
          "Load",
          `${route.crates} of ${capacity[route.vehicle.id] ?? "?"} crates · ${plural(route.bags, "bag", "bags")}`,
        ]
      : null,
    route
      ? [
          "Stops",
          `${route.stops.filter((s) => s.status === "handed_over" || s.status === "skipped").length} of ${route.stops.length} done`,
        ]
      : null,
  ].filter((r) => r !== null);
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
      <h2 className="text-[17px] leading-6 font-bold">Solver</h2>
      {rows.length > 0 ? (
        <dl className="m-0">
          {rows.map(([k, v]) => (
            <div
              key={k}
              className="flex justify-between gap-3 border-b border-line py-1.5 text-sm last:border-b-0"
            >
              <dt className="text-fg-secondary">{k}</dt>
              <dd className="m-0 text-right font-bold">{v}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-sm leading-5 text-fg-secondary">
          Nothing has been solved for this day yet.
        </p>
      )}
      {plan?.source && plan.source in SOURCE_NOTE && (
        <p className="text-sm leading-5 text-fg-secondary">
          {SOURCE_NOTE[/** @type {keyof typeof SOURCE_NOTE} */ (plan.source)]}
        </p>
      )}
    </section>
  );
}

const PLAN_BADGE = /** @type {const} */ ({
  queued: ["outline", "Waiting to solve"],
  solving: ["accent", "Solving"],
  solved: ["accent", "Solved, not published"],
  published: ["primary", "Published"],
  failed: ["danger", "Failed"],
  superseded: ["neutral", "Replaced"],
});

/**
 * The day's plan: its state, its problems, and for a hub manager the buttons to solve again and to publish.
 * @param {{ plan: import("./data.js").RoutePlan | null, date: string, canPlan: boolean }} props
 */
function PlanPanel({ plan, date, canPlan }) {
  const router = useRouter();
  const [busy, start] = useTransition();
  // One Idempotency-Key per click on a button; a new one only after the answer came.
  const keys = useRef({ solve: "", publish: "" });
  const solving = plan?.status === "queued" || plan?.status === "solving";
  const problems = plan?.summary?.problems ?? [];

  // A solve runs in the background (202): poll the plan until it is done, then show the result.
  const planId = plan?.id;
  useEffect(() => {
    if (!planId || !solving) return;
    const timer = setInterval(async () => {
      const status = await planStatus(planId);
      if (status && status !== "queued" && status !== "solving") router.refresh();
    }, SOLVE_POLL_MS);
    return () => clearInterval(timer);
  }, [planId, solving, router]);

  function solve() {
    keys.current.solve ||= newKey();
    start(async () => {
      const r = await solvePlan(keys.current.solve, date);
      if (r.ok) keys.current.solve = "";
      toast(r.ok ? "Solving the routes. This takes a few seconds." : r.message);
    });
  }

  function publish() {
    if (!plan) return;
    keys.current.publish ||= newKey();
    start(async () => {
      const r = await publishPlan(keys.current.publish, plan.id, problems.length > 0);
      if (r.ok) keys.current.publish = "";
      toast(r.ok ? "Published. Riders and the packing floor follow the new routes." : r.message);
    });
  }

  const [tone, label] = plan
    ? PLAN_BADGE[plan.status]
    : /** @type {const} */ (["outline", "No plan yet"]);
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">Plan</h2>
        <Badge tone={tone}>{label}</Badge>
      </div>
      {plan?.status === "failed" && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {plan.error ?? "The solve failed."}
        </p>
      )}
      {plan?.status === "solved" && problems.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-sm leading-5 font-semibold">The solver found problems:</p>
          <ul className="m-0 list-disc pl-5 text-sm leading-5">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <p className="text-sm leading-5 text-fg-secondary">
            Publishing anyway accepts them, and the decision is recorded.
          </p>
        </div>
      )}
      {plan?.status === "solved" && problems.length === 0 && (
        <p className="text-sm leading-5 text-fg-secondary">
          No problems found. Publish it to send the routes to the riders.
        </p>
      )}
      {plan?.status === "published" && (
        <p className="text-sm leading-5 text-fg-secondary">
          Solving again replaces this plan while no route has started.
        </p>
      )}
      {canPlan ? (
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" block isDisabled={busy || solving} onPress={solve}>
            {solving ? "Solving…" : plan ? "Solve again" : "Solve now"}
          </Button>
          <Button
            variant="primary"
            block
            isDisabled={busy || plan?.status !== "solved"}
            onPress={publish}
          >
            {problems.length > 0 ? "Publish anyway" : "Publish"}
          </Button>
        </div>
      ) : (
        <p className="text-sm leading-5 text-fg-secondary">
          Only the hub’s manager solves and publishes plans.
        </p>
      )}
    </section>
  );
}
