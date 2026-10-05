"use client";

import { EmptyState, RouteStopCard, SegmentedControl, Select } from "@gharbasket/ui";
import { MapPanel } from "@gharbasket/ui/console";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useEffect, useMemo, useState, useTransition } from "react";
import { formatDay, formatKm, formatTime, plural, SLOTS } from "@/lib/fleet.js";
import "@/lib/maps.js";
import { vanPosition } from "./actions.js";

const POLL_MS = 15_000;
const LEGEND = [
  { kind: /** @type {const} */ ("hub"), label: "Hub" },
  { kind: /** @type {const} */ ("stop"), label: "Stop" },
  { kind: /** @type {const} */ ("van"), label: "Van now" },
  { kind: /** @type {const} */ ("route"), label: "Planned route" },
];

/** @param {import("./data.js").RouteStop} s */
function windowText(s) {
  return s.handed_over_at
    ? `handed over ${formatTime(s.handed_over_at)}`
    : `ETA ${formatTime(s.eta)}`;
}

/** @param {import("./data.js").RouteStop} s */
function mapLabel(s) {
  return s.handed_over_at
    ? `${s.society} · done ${formatTime(s.handed_over_at)}`
    : `${s.society} · ETA ${formatTime(s.eta)}`;
}

/**
 * The live map and stop list of one slot's route, the way the rider and the captains see it.
 * @param {{ slot: "am" | "pm", date: string, routes: import("./data.js").RouteRecord[] }} props
 */
export function RoutesView({ slot, date, routes }) {
  const [pending, startTransition] = useTransition();
  const [, setSlot] = useQueryState(
    "slot",
    parseAsStringLiteral(["am", "pm"])
      .withDefault("am")
      .withOptions({ shallow: false, startTransition }),
  );
  const [picked, setPicked] = useState(routes[0]?.id ?? "");
  const route = routes.find((r) => r.id === picked) ?? routes[0];
  const slotLabel = SLOTS.find((s) => s.value === slot)?.label ?? "";

  // The van moves: ask for its position every 15 seconds while the route is out, and keep the last one if a call fails.
  const [van, setVan] = useState(route?.van ?? null);
  const [prev, setPrev] = useState(route?.id);
  if (route?.id !== prev) {
    setPrev(route?.id);
    setVan(route?.van ?? null);
  }
  const routeId = route?.id;
  const running = route?.status === "in_progress";
  useEffect(() => {
    if (!routeId || !running) return;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      const next = await vanPosition(routeId);
      if (next) setVan(next);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [routeId, running]);

  const markers = useMemo(() => {
    if (!route) return [];
    return [
      {
        id: "hub",
        kind: /** @type {const} */ ("hub"),
        label: route.hub.name,
        lat: route.hub.lat,
        lng: route.hub.lng,
      },
      ...route.stops.map((s) => ({
        id: `stop-${s.seq}`,
        kind: /** @type {const} */ ("stop"),
        n: s.seq,
        label: mapLabel(s),
        lat: s.lat,
        lng: s.lng,
      })),
      ...(van
        ? [
            {
              id: "van",
              kind: /** @type {const} */ ("van"),
              label: `${route.rider.name.split(" ")[0]} · ${formatTime(van.at)}`,
              lat: van.lat,
              lng: van.lng,
            },
          ]
        : []),
    ];
  }, [route, van]);
  const line = useMemo(
    () => (route ? ["hub", ...route.stops.map((s) => `stop-${s.seq}`), "hub"] : []),
    [route],
  );

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            Routes · {formatDay(date)}, {slotLabel}
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {route
              ? `Published at ${formatTime(route.published_at)} by the route solver. The van’s position updates every 15 seconds while it’s out.`
              : "Each slot’s route is solved from the packed boxes and published for the rider."}
          </p>
        </div>
        <SegmentedControl
          label="Slot"
          options={SLOTS}
          value={slot}
          onChange={(v) => setSlot(v === "pm" ? "pm" : "am")}
        />
      </div>

      <div
        className={`transition-opacity motion-reduce:transition-none ${pending ? "opacity-60" : ""}`}
        aria-busy={pending}
      >
        {!route ? (
          <div className="rounded-md bg-surface p-4">
            <EmptyState
              title={`No route for ${slotLabel} yet`}
              body="The solver publishes a route once the slot’s boxes are packed. Check the packing board if it is late."
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
                {van ? `${route.rider.name}’s van, last seen ${formatTime(van.at)}` : ""}
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
                    label: `Route ${i + 1} · ${r.rider.name}`,
                  }))}
                />
              )}
              <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
                <h2 className="text-[17px] leading-6 font-bold">
                  Route {routes.indexOf(route) + 1} · {route.rider.name} · {route.vehicle.name}
                </h2>
                <p className="-mt-2 text-sm leading-5 text-fg-secondary">
                  {route.vehicle.registration}
                </p>
                {route.stops.map((s) => (
                  <RouteStopCard
                    key={s.seq}
                    n={s.seq}
                    society={s.society}
                    where={s.where}
                    captain={s.status === "current" ? undefined : (s.captain ?? undefined)}
                    window={windowText(s)}
                    crates={s.crates}
                    bags={s.bags}
                    status={s.status}
                    onNavigate={() =>
                      window.open(
                        `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lng}`,
                        "_blank",
                        "noopener",
                      )
                    }
                  />
                ))}
              </section>
              <SolverPanel stats={route.solver_stats} />
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/** @param {{ stats: import("./data.js").SolverStats }} props */
function SolverPanel({ stats }) {
  const returnLeg = [
    stats.empty_crates_on_return ? "Empty crates" : null,
    stats.donation_pickups > 0
      ? plural(stats.donation_pickups, "donation pickup", "donation pickups")
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const rows = [
    ["Distance", formatKm(stats.distance_m)],
    ["Drive time", `${stats.drive_min} min`],
    ["Load", `${stats.crates} of ${stats.capacity_crates} crates`],
    ["Return leg", returnLeg || "Nothing to collect"],
  ];
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
      <h2 className="text-[17px] leading-6 font-bold">Solver</h2>
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
      {stats.solver === "fallback" && (
        <p className="text-sm leading-5 text-fg-secondary">
          The solver was unavailable, so this is yesterday’s stop order.
        </p>
      )}
    </section>
  );
}
