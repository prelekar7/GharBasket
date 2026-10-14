"use client";

import { Button, EmptyState, Select, Toaster, toast } from "@gharbasket/ui";
import { FilterBar, KanbanBoard, KpiTile } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useState, useTransition } from "react";
import { plural } from "@/lib/format.js";
import { dayLabel } from "@/lib/when.js";
import { buildTasks, releaseTask } from "./actions.js";

/**
 * GET /v1/hubs/{id}/packing-tasks and /packing-summary (M6 §3.2, built). A task is `queued`, `picking` or
 * `packed`; a picking task with no packer was released and waits for any packer. `crate_no` (set when
 * a bag is packed) counts crates within the society.
 * @typedef {"to_pack" | "packing" | "packed"} Column
 * @typedef {{ id: string, seq: number, society: { id: string, name: string }, flat_label: string | null,
 *   slot: { label: string } | null, status: "queued" | "picking" | "packed",
 *   packer: { user_id: string, name: string | null } | null, bags: number, crate_no: number | null,
 *   lines_total: number, lines_done: number }} PackingTask
 * @typedef {{ date: string, queued: number, picking: number, packed: number,
 *   societies: { society_id: string, name: string, bags: number, packed_bags: number, crates: number }[] }} Summary
 */

/** Cards shown per column; the tiles above carry the full counts. */
const SHOWN = 3;
const TITLES = { to_pack: "To pack", packing: "Packing", packed: "Packed", crates: "Crates" };

/** The board column of a task: released bags (picking, nobody holding) wait with the queue. @param {PackingTask} t @returns {Column} */
const columnOf = (t) =>
  t.status === "packed" ? "packed" : t.status === "picking" && t.packer ? "packing" : "to_pack";

const key = () => crypto.randomUUID();

/** @param {{ date: string, days: string[], summary: Summary, tasks: PackingTask[] }} props */
export function PackingView({ date, days, summary, tasks: initial }) {
  const router = useRouter();
  const [tasks, setTasks] = useState(initial);
  useEffect(() => setTasks(initial), [initial]);
  const [, setDate] = useQueryState("date", parseAsString.withOptions({ shallow: false }));
  const [society, setSociety] = useQueryState("society", parseAsString.withDefault("all"));
  const [packer, setPacker] = useQueryState("packer", parseAsString.withDefault("all"));
  const [busy, startTransition] = useTransition();

  const inColumn = (/** @type {Column} */ c) => tasks.filter((t) => columnOf(t) === c);
  const visible = tasks
    .filter(
      (t) =>
        (society === "all" || t.society.id === society) &&
        (packer === "all" || t.packer?.user_id === packer),
    )
    .sort((a, b) => a.seq - b.seq);
  const packers = [
    ...new Map(tasks.filter((t) => t.packer).map((t) => [t.packer?.user_id, t.packer?.name])),
  ];
  const working = [...new Set(inColumn("packing").map((t) => t.packer?.name))];
  const crates = summary.societies.reduce((n, s) => n + s.crates, 0);

  /** @param {PackingTask} t */
  const card = (t) => ({
    id: t.id,
    title: `#${t.seq} · ${t.flat_label ?? "No address"}`,
    meta: `${t.society.name}${t.slot ? ` · ${t.slot.label}` : ""}${t.status === "packed" ? "" : ` · ${t.lines_done} of ${plural(t.lines_total, "line")}`}`,
    badge:
      columnOf(t) === "packing" && t.packer?.name
        ? { tone: /** @type {const} */ ("accent"), label: t.packer.name.split(" ")[0] }
        : t.status === "picking"
          ? { tone: /** @type {const} */ ("outline"), label: "Released" }
          : t.status === "packed" && t.crate_no
            ? { tone: /** @type {const} */ ("primary"), label: `Crate ${t.crate_no}` }
            : undefined,
  });
  const columns = [
    { id: "to_pack", title: TITLES.to_pack, empty: "Every bag is packed." },
    { id: "packing", title: TITLES.packing, empty: undefined },
    { id: "packed", title: TITLES.packed, empty: undefined },
  ].map((c) => ({
    ...c,
    limit: undefined,
    cards: visible
      // The bags packed last are the ones the packer is likely to need again, so they come first.
      .filter((t) => columnOf(t) === c.id)
      .sort((a, b) => (c.id === "packed" ? b.seq - a.seq : a.seq - b.seq))
      .slice(0, SHOWN)
      .map(card),
  }));
  columns.push({
    id: "crates",
    title: TITLES.crates,
    empty: undefined,
    limit: undefined,
    cards:
      packer !== "all"
        ? []
        : summary.societies
            .filter((s) => s.crates > 0 && (society === "all" || s.society_id === society))
            .map((s) => ({
              id: `crates:${s.society_id}`,
              title: s.name,
              meta: `${plural(s.crates, "crate")} · ${s.packed_bags} of ${plural(s.bags, "bag")} packed`,
              badge: undefined,
            })),
  });

  /** Releases a bag (optimistically): the packer's hold is cleared, and the order stays in picking. @param {string} id */
  function release(id) {
    const before = tasks.find((t) => t.id === id);
    if (!before) return;
    setTasks((all) => all.map((t) => (t.id === id ? { ...t, packer: null } : t)));
    startTransition(async () => {
      const res = await releaseTask(id, key());
      if (!res.ok) {
        setTasks((all) => all.map((t) => (t.id === id ? before : t)));
        toast(res.message);
        return;
      }
      toast(`#${before.seq} released. Any packer can take it now.`);
      router.refresh();
    });
  }

  /** @param {string} cardId @param {string} from @param {string} to */
  const onMove = (cardId, from, to) => {
    if (from === "packing" && to === "to_pack") release(cardId);
    else
      toast(
        "Packers start and finish bags in their app, by scanning the bag. Here you can send a bag being packed back to the queue.",
      );
  };

  function build() {
    startTransition(async () => {
      const res = await buildTasks(date, key());
      if (!res.ok) {
        toast(res.message);
        return;
      }
      const { created, existing } = res.data;
      toast(
        created
          ? `${plural(created, "new bag")} added to the queue (${existing} were already there).`
          : `Nothing new to add. ${plural(existing, "bag")} already built.`,
      );
      router.refresh();
    });
  }

  const active = [
    ...(society !== "all"
      ? [
          {
            key: "society",
            label: summary.societies.find((s) => s.society_id === society)?.name ?? society,
          },
        ]
      : []),
    ...(packer !== "all"
      ? [{ key: "packer", label: packers.find(([id]) => id === packer)?.[1] ?? packer }]
      : []),
  ];
  const total = summary.queued + summary.picking + summary.packed;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Packing · {dayLabel(date)}
          </h1>
          <p className="m-0 max-w-[60ch] text-sm text-fg-secondary">
            Bags are built at 5:00, one per box or add-on order, in queue order: slot, society,
            tower and flat. Packers take the next bag in their app.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-[200px]">
            <Select
              label="Delivery date"
              options={days.map((d) => ({ value: d, label: dayLabel(d) }))}
              value={date}
              onChange={(v) => setDate(v)}
            />
          </div>
          <Button variant="primary" onPress={build} isDisabled={busy}>
            Build bags now
          </Button>
        </div>
      </div>

      {total === 0 ? (
        <EmptyState
          title="No bags for this day yet"
          body="Bags are made from the orders locked at the cut-off. Build them now if the orders are already locked."
          action="Build bags now"
          onAction={build}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4">
            <KpiTile label="To pack" value={String(summary.queued)} hint="In queue order" />
            <KpiTile
              label="Packing now"
              value={String(summary.picking)}
              hint={working.length ? `${working.join(", ")} packing` : "No one has a bag open"}
            />
            <KpiTile label="Packed" value={String(summary.packed)} hint="Labelled and in a crate" />
            <KpiTile
              label="Crates"
              value={String(crates)}
              hint={
                summary.societies.map((s) => `${s.name} ${s.crates}`).join(" · ") || "No crate open"
              }
            />
          </div>

          <FilterBar
            filters={[
              {
                key: "society",
                label: "Society",
                value: society,
                options: [
                  { value: "all", label: "All societies" },
                  ...summary.societies.map((s) => ({ value: s.society_id, label: s.name })),
                ],
              },
              {
                key: "packer",
                label: "Packer",
                value: packer,
                options: [
                  { value: "all", label: "Everyone" },
                  ...packers.map(([id, name]) => ({ value: String(id), label: String(name) })),
                ],
              },
            ]}
            onFilterChange={(k, v) => (k === "society" ? setSociety(v) : setPacker(v))}
            active={active}
            onRemove={(k) => (k === "society" ? setSociety(null) : setPacker(null))}
            onClear={() => {
              setSociety(null);
              setPacker(null);
            }}
            count={visible.length}
            labels={{ results: "{n} bags" }}
          />

          {/* The board scrolls sideways inside this box; its screen-reader-only text is absolutely
              positioned, so the box also clips it or the whole page would scroll sideways on a phone. */}
          <div className="relative min-w-0 overflow-x-clip">
            <KanbanBoard label="Packing board" columns={columns} onMove={onMove} />
          </div>
          <p className="m-0 text-sm text-fg-secondary">
            Each column shows its next {SHOWN} bags; the tiles above have the full counts. A bag
            moves by scanning in the packer app. Here, “Move” on a bag being packed sends it back to
            the queue. Crates are counted when a bag is packed, three bags to a crate.
          </p>
        </>
      )}
      <Toaster />
    </div>
  );
}
