"use client";

import { OrderTimeline } from "@gharbasket/ui";
import { DataTable, KpiTile, MapPanel } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { formatDay, plural } from "@/lib/format.js";
import "@/lib/maps.js";

/**
 * GET /v1/hubs/{id}/ops-board?date= (our proposal; M12 US-1 asks for live counts per stage).
 * @typedef {{
 *   hub_id: string, date: string, as_of: string, status_line: string,
 *   boxes: { total: number, by_society: { name: string, short_name: string, boxes: number }[], locked_at: string },
 *   bags: { packed: number, total: number, finished_at: string | null, packers: string },
 *   short: { items: { product: string, bags: number }[] },
 *   waste: { kg: number, change_kg: number, versus: string },
 *   steps: { label: string, time?: string, note?: string, state?: "done" | "current" | "issue" | "todo" }[],
 *   markers: { id: string, kind: "hub" | "society" | "van" | "stop", label: string, lng: number, lat: number, status?: "live" | "unlocking" | "prospect" }[],
 *   route: string[],
 *   attention: { id: string, what: string, where: string, state: { tone: "neutral" | "accent" | "primary" | "danger" | "outline", label: string }, next: { text: string, href?: string } }[],
 * }} OpsBoard
 */

const COLUMNS = [
  { key: "what", label: "What" },
  { key: "where", label: "Where" },
  { key: "state", label: "State" },
  { key: "next", label: "Next step" },
];

/** How often the board asks the server for fresh counts. */
const REFRESH_MS = 60_000;

/** @param {{ board: OpsBoard }} props */
export function OpsBoardView({ board }) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [router]);

  const { boxes, bags, short, waste } = board;
  const shortBags = short.items.reduce((n, i) => n + i.bags, 0);
  const rows = board.attention.map((a) => ({
    id: a.id,
    what: { text: a.what, strong: true },
    where: a.where,
    state: { badge: a.state },
    next: a.next.href ? { text: a.next.text, href: a.next.href } : a.next.text,
  }));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Today · {formatDay(board.date)}
        </h1>
        <p className="m-0 text-sm text-fg-secondary">
          {board.as_of}. {board.status_line}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4">
        <KpiTile
          label="Boxes today"
          value={String(boxes.total)}
          delta={{
            value: boxes.by_society.map((s) => `${s.boxes} ${s.short_name}`).join(" · "),
            direction: "flat",
          }}
          hint={`Locked at the ${boxes.locked_at}`}
        />
        <KpiTile
          label="Bags packed"
          value={`${bags.packed} of ${bags.total}`}
          delta={{
            value: bags.finished_at ? `done ${bags.finished_at}` : "still packing",
            direction: "flat",
          }}
          hint={bags.packers}
        />
        <KpiTile
          label="Short items"
          value={String(short.items.length)}
          delta={{
            value: short.items.map((i) => `${i.product} · ${plural(i.bags, "bag")}`).join(", "),
            direction: "flat",
          }}
          hint={shortBags > 0 ? "Swaps or wallet refunds applied" : "Nothing short today"}
        />
        <KpiTile
          label="Waste so far"
          value={`${waste.kg} kg`}
          delta={{
            value: `${waste.change_kg > 0 ? "+" : "−"}${Math.abs(waste.change_kg)} kg`,
            direction: waste.change_kg > 0 ? "up" : "down",
            good: waste.change_kg <= 0,
            note: `vs ${waste.versus}`,
          }}
          hint="Spoiled at receiving; cascade runs at 12:30"
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="m-0 text-[17px] leading-6 font-bold">The day so far</h2>
          <OrderTimeline steps={board.steps} label="Hub day" />
        </section>
        <div className="flex min-w-0 flex-col gap-4">
          <MapPanel
            label="Van and stops now"
            height={260}
            markers={board.markers}
            route={board.route}
          />
          <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
            <h2 className="m-0 text-[17px] leading-6 font-bold">Needs you</h2>
            <DataTable
              columns={COLUMNS}
              rows={rows}
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
