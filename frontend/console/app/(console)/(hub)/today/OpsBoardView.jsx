"use client";

import { OrderTimeline } from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { formatDay, formatTime, plural } from "@/lib/format.js";
import { attentionRows, statusLine, timelineSteps, wasteChange } from "./board.js";

const COLUMNS = [
  { key: "what", label: "What" },
  { key: "where", label: "Where" },
  { key: "state", label: "State" },
  { key: "next", label: "Next step" },
];

/** How often the board asks the server for fresh counts. */
const REFRESH_MS = 60_000;

/**
 * The hub manager's morning in one place: the day's counts, the steps so far and what needs them. The
 * counts come from GET /v1/analytics/hubs/{id}/today and the page asks again every minute.
 * @param {{ board: import("./board.js").Today }} props
 */
export function OpsBoardView({ board }) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [router]);

  const { boxes, packing, shortages, routes, waste } = board;
  const shortBags = shortages.reduce((n, i) => n + i.bags, 0);
  const { change_kg, versus } = wasteChange(waste);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Today · {formatDay(board.date)}
        </h1>
        <p className="m-0 text-sm text-fg-secondary">
          {formatTime(board.as_of)}. {statusLine(board.steps)}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4">
        <KpiTile
          label="Boxes today"
          value={String(boxes.total)}
          delta={{
            value: boxes.by_society.map((s) => `${s.boxes} ${s.name}`).join(" · "),
            direction: "flat",
          }}
          hint={`${boxes.locked} locked at the ${formatTime(boxes.cutoff_at)} cut-off`}
        />
        <KpiTile
          label="Bags packed"
          value={`${packing.bags_packed} of ${packing.bags_total}`}
          delta={{
            value: packing.finished_at
              ? `done ${formatTime(packing.finished_at)}`
              : "still packing",
            direction: "flat",
          }}
          hint={packing.finished_at ? "Packing finished" : "Packing in progress"}
        />
        <KpiTile
          label="Short items"
          value={String(shortages.length)}
          delta={{
            value: shortages.map((i) => `${i.name} · ${plural(i.bags, "bag")}`).join(", "),
            direction: "flat",
          }}
          hint={shortBags > 0 ? "Swaps or wallet refunds applied" : "Nothing short today"}
        />
        <KpiTile
          label="Waste so far"
          value={`${waste.kg} kg`}
          delta={
            versus
              ? {
                  value: `${change_kg > 0 ? "+" : "−"}${Math.abs(change_kg)} kg`,
                  direction: change_kg > 0 ? "up" : change_kg < 0 ? "down" : "flat",
                  good: change_kg <= 0,
                  note: `vs ${versus}`,
                }
              : undefined
          }
          hint="Spoiled at receiving; cascade runs at 12:30"
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">The day so far</h2>
          <OrderTimeline steps={timelineSteps(board.steps)} label="Hub day" />
        </section>
        <div className="flex min-w-0 flex-col gap-4">
          <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
            <h2 className="m-0 text-[17px] leading-6 font-bold">Vans and stops now</h2>
            <p className="m-0 text-sm leading-5 text-fg-secondary">
              {plural(routes.started, "route")} out of {routes.planned} planned, {routes.completed}{" "}
              completed. {routes.stops_done} of {plural(routes.stops_total, "stop")} done
              {routes.late_stops > 0 ? `, ${routes.late_stops} late` : ", none late"}. The map of
              the vans is on{" "}
              <Link href="/routes" className="underline">
                Routes
              </Link>
              .
            </p>
          </section>
          <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
            <h2 className="m-0 text-[17px] leading-6 font-bold">Needs you</h2>
            <DataTable
              columns={COLUMNS}
              rows={attentionRows(board.attention)}
              dense
              caption="Items that need the hub manager"
              labels={{ empty: "Nothing needs you right now." }}
            />
          </section>
        </div>
      </div>
    </div>
  );
}
