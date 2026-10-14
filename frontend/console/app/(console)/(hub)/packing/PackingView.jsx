"use client";

import { Button, Toaster, toast } from "@gharbasket/ui";
import { FilterBar, KanbanBoard, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useState, useTransition } from "react";
import { formatDay, plural } from "@/lib/format.js";
import { assignNext, moveTask, printLabels } from "./actions.js";

/**
 * GET /v1/hubs/{id}/packing-tasks (M6 §3). `items` follow the spec's table; `crates`, `packers`,
 * `label_sheets` and `as_of` are our additions for the board.
 * @typedef {"to_pack" | "packing" | "packed" | "crated"} PackStatus
 * @typedef {{ id: string, bag_qr: string, flat: string, society_id: string, society_name: string,
 *   stop: number, route_seq: number, status: PackStatus, packer_id: string | null,
 *   packer_name: string | null, crate: string | null, item_count: number, has_tiffin: boolean,
 *   flags: string[] }} PackingTask
 * @typedef {{ id: string, society_id: string, society_name: string, capacity: number }} Crate
 * @typedef {{ date: string, as_of: string, items: PackingTask[], crates: Crate[],
 *   packers: { id: string, name: string }[], label_sheets: number }} PackingBoard
 */

/** Cards shown per column; the tiles above carry the full counts. */
const SHOWN = 3;
const TITLES = { to_pack: "To pack", packing: "Packing", packed: "Packed", crated: "In crates" };
const STOPS = [
  { value: "all", label: "All stops" },
  { value: "1", label: "1 · Sunrise Towers" },
  { value: "2", label: "2 · Krishna Kunj" },
];

const key = () => crypto.randomUUID();

/** @param {{ board: PackingBoard }} props */
export function PackingView({ board }) {
  const [tasks, setTasks] = useState(board.items);
  useEffect(() => setTasks(board.items), [board.items]);
  const [stop, setStop] = useQueryState("stop", parseAsString.withDefault("all"));
  const [packer, setPacker] = useQueryState("packer", parseAsString.withDefault("all"));
  const [busy, startTransition] = useTransition();

  const byStatus = (/** @type {PackStatus} */ s) => tasks.filter((t) => t.status === s);
  const stopOf = (/** @type {string} */ society) => (society === "sunrise-towers" ? "1" : "2");
  const visible = tasks
    .filter(
      (t) =>
        (stop === "all" || String(t.stop) === stop) && (packer === "all" || t.packer_id === packer),
    )
    .sort((a, b) => a.route_seq - b.route_seq);
  // The bags packed last are the ones the packer is likely to need again, so they come first.
  const ordered = (/** @type {PackStatus} */ s) =>
    s === "packed" ? [...visible].reverse() : visible;

  // Crates fill from the bags in them.
  const crates = board.crates.map((c) => ({
    ...c,
    filled: tasks.filter((t) => t.crate === c.id).length,
  }));
  const filling = crates.filter((c) => c.filled > 0 && c.filled < c.capacity);
  const working = [...new Set(byStatus("packing").map((t) => t.packer_name))];

  /** @param {PackingTask} t */
  const card = (t) => ({
    id: t.id,
    title: `${t.bag_qr} · ${t.flat}`,
    meta: `${t.society_name} · stop ${t.stop}${t.status === "to_pack" || t.status === "packing" ? ` · ${plural(t.item_count, "item")}${t.has_tiffin ? " + tiffin" : ""}` : ""}`,
    badge:
      t.status === "packing" && t.packer_name
        ? { tone: /** @type {const} */ ("accent"), label: t.packer_name.split(" ")[0] }
        : t.status === "packed"
          ? { tone: /** @type {const} */ ("primary"), label: "Labelled" }
          : undefined,
    flag: t.flags.includes("methi_short") ? "Methi short" : undefined,
  });
  const columns = /** @type {PackStatus[]} */ (["to_pack", "packing", "packed"]).map((s) => ({
    id: s,
    title: TITLES[s],
    limit: s === "packing" ? board.packers.length : undefined,
    empty: s === "to_pack" ? "Every bag is packed." : undefined,
    cards: ordered(s)
      .filter((t) => t.status === s)
      .slice(0, SHOWN)
      .map(card),
  }));
  columns.push({
    id: "crated",
    title: TITLES.crated,
    limit: undefined,
    empty: undefined,
    cards:
      packer !== "all"
        ? []
        : crates
            .filter((c) => c.filled > 0 && (stop === "all" || stopOf(c.society_id) === stop))
            .map((c) => ({
              id: `crate:${c.id}`,
              title: `Crate ${c.id}`,
              meta: `${c.filled} of ${c.capacity} bags · ${c.society_name}`,
              badge: undefined,
              flag: undefined,
            })),
  });

  /** @param {PackingTask} updated */
  const replace = (updated) =>
    setTasks((all) => all.map((t) => (t.id === updated.id ? updated : t)));

  /**
   * Moves a bag (optimistically), tells the server, and offers Undo.
   * @param {string} id @param {PackStatus} to @param {boolean} [undo] true when this is an undo
   */
  function move(id, to, undo = false) {
    const before = tasks.find((t) => t.id === id);
    if (!before || before.status === to) return;
    setTasks((all) => all.map((t) => (t.id === id ? { ...t, status: to } : t)));
    startTransition(async () => {
      const res = await moveTask(id, to, key());
      if (!res.ok) {
        replace(before);
        toast(res.message);
        return;
      }
      replace(res.data);
      if (!undo)
        toast(`Moved ${before.bag_qr} to ${TITLES[to]}`, {
          action: { label: "Undo", onClick: () => move(id, before.status, true) },
        });
    });
  }

  /** @param {string} cardId @param {string} _from @param {string} to */
  const onMove = (cardId, _from, to) => {
    if (cardId.startsWith("crate:")) {
      toast("Crates close at the van, when the bags are scanned in.");
      return;
    }
    move(cardId, /** @type {PackStatus} */ (to));
  };

  function assign() {
    startTransition(async () => {
      const res = await assignNext(key());
      if (!res.ok) {
        toast(res.message);
        return;
      }
      replace(res.data);
      toast(`${res.data.bag_qr} went to ${res.data.packer_name}`);
    });
  }

  function print() {
    startTransition(async () => {
      const res = await printLabels(key());
      toast(res.ok ? "Label sheets sent to the hub printer." : res.message);
    });
  }

  const active = [
    ...(stop !== "all"
      ? [{ key: "stop", label: STOPS.find((s) => s.value === stop)?.label ?? stop }]
      : []),
    ...(packer !== "all"
      ? [{ key: "packer", label: board.packers.find((p) => p.id === packer)?.name ?? packer }]
      : []),
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
            Packing · {formatDay(board.date)}
          </h1>
          <p className="m-0 max-w-[60ch] text-sm text-fg-secondary">
            {board.as_of}. Tasks were made at 5:00 in route order, so the first stop’s bags are
            packed first and loaded last.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onPress={print} isDisabled={busy}>
            Print labels · {plural(board.label_sheets, "sheet")}
          </Button>
          <Button variant="primary" onPress={assign} isDisabled={busy}>
            Assign next bags
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 min-[640px]:grid-cols-2 min-[1100px]:grid-cols-4">
        <KpiTile label="To pack" value={String(byStatus("to_pack").length)} hint="Stop 1 first" />
        <KpiTile
          label="Packing now"
          value={String(byStatus("packing").length)}
          hint={working.length ? `${working.join(", ")} packing` : "No one has a bag open"}
        />
        <KpiTile
          label="Packed"
          value={String(byStatus("packed").length)}
          hint="Labelled, waiting for a crate"
        />
        <KpiTile
          label="In crates"
          value={String(byStatus("crated").length)}
          hint={
            filling.length
              ? `Crate ${filling.map((c) => c.id).join(", ")} filling`
              : "No crate open"
          }
        />
      </div>

      <FilterBar
        filters={[
          { key: "stop", label: "Stop", value: stop, options: STOPS },
          {
            key: "packer",
            label: "Packer",
            value: packer,
            options: [
              { value: "all", label: "Everyone" },
              ...board.packers.map((p) => ({ value: p.id, label: p.name })),
            ],
          },
        ]}
        onFilterChange={(k, v) => (k === "stop" ? setStop(v) : setPacker(v))}
        active={active}
        onRemove={(k) => (k === "stop" ? setStop(null) : setPacker(null))}
        onClear={() => {
          setStop(null);
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
        Each column shows its next {SHOWN} bags; the tiles above have the full counts. A bag moves
        by scanning in the packer app, or with “Move to…” here.
      </p>
      <Toaster />
    </div>
  );
}
