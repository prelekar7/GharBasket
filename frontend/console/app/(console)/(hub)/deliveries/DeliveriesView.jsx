"use client";

import { DataTable, FilterBar, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useMemo } from "react";
import { formatDay, formatTime } from "@/lib/fleet.js";

/** @type {Record<import("./data.js").BagState, "delivered" | "ready" | "out" | "issue">} */
const PILL = { door: "delivered", lobby: "ready", with_captain: "out", issue: "issue" };

const SHOW = [
  { value: "open", label: "Not at the door" },
  { value: "all", label: "All bags" },
];

const COLUMNS = [
  { key: "bag", label: "Bag", sortable: true },
  { key: "flat", label: "Flat", sortable: true },
  { key: "status", label: "State" },
  { key: "proof", label: "Proof" },
  { key: "at", label: "Time", sortable: true },
  { key: "next", label: "Next" },
];

/**
 * Every bag from handover to the door, filtered to what needs attention by default.
 * @param {{ date: string, as_of: string, summary: import("./data.js").DeliverySummary, items: import("./data.js").Delivery[] }} props
 */
export function DeliveriesView({ date, as_of, summary, items }) {
  const [q, setQ] = useQueryState(
    "q",
    parseAsString.withDefault("").withOptions({ clearOnDefault: true }),
  );
  const [society, setSociety] = useQueryState(
    "society",
    parseAsString.withDefault("all").withOptions({ clearOnDefault: true }),
  );
  const [show, setShow] = useQueryState(
    "show",
    parseAsStringLiteral(["open", "all"]).withDefault("open").withOptions({ clearOnDefault: true }),
  );

  const societies = useMemo(
    () => [...new Map(items.map((d) => [d.society_id, d.society])).entries()],
    [items],
  );
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return items
      .filter((d) => (show === "all" ? true : d.state !== "door"))
      .filter((d) => society === "all" || d.society_id === society)
      .filter((d) => !needle || `${d.bag} ${d.flat}`.toLowerCase().includes(needle))
      .map((d) => ({
        id: d.bag,
        __tone: d.state === "issue" ? /** @type {const} */ ("danger") : undefined,
        bag: { text: d.bag, strong: true },
        flat: { text: d.flat, sub: d.society },
        status: { status: PILL[d.state], text: d.state_text },
        proof: d.proof,
        at: { text: formatTime(d.at), sort: d.at },
        next: d.next ? (d.next.href ? { text: d.next.text, href: d.next.href } : d.next.text) : "—",
      }));
  }, [items, q, society, show]);

  const problem = items.find((d) => d.state === "issue");
  const problems = items.filter((d) => d.state === "issue");
  const problemHint = problem
    ? `${problem.flat}, ${problem.state_text.toLowerCase()}${problems.length > 1 ? ` and ${problems.length - 1} more` : ""}`
    : "Nothing to fix";

  const active = [
    ...(show === "open" ? [{ key: "show", label: "Not at the door" }] : []),
    ...(society !== "all"
      ? [{ key: "society", label: societies.find(([id]) => id === society)?.[1] ?? society }]
      : []),
  ];
  const sentence = show === "open" ? " (filtered to anything not handed over at the door)" : "";

  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          Deliveries · {formatDay(date)}
        </h1>
        <p className="text-sm leading-5 text-fg-secondary">
          {formatTime(as_of)}. Every bag from handover to the door, with its proof. Flats and bags
          only; customer phone numbers are never shown here.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <KpiTile
          label="At the door"
          value={String(summary.at_door)}
          hint="Scanned by the captain"
        />
        <KpiTile
          label="In the lobby"
          value={String(summary.in_lobby)}
          hint={`Collect by ${formatTime(summary.lobby_collect_by).replace(":00", "")}`}
        />
        <KpiTile label="Problems" value={String(summary.problems)} hint={problemHint} />
        {summary.with_captain.map((w) => (
          <KpiTile
            key={w.society_id}
            label={w.society}
            value={String(w.bags)}
            hint={`Handed over ${formatTime(w.handed_over_at)}; captain delivering`}
          />
        ))}
      </div>

      <FilterBar
        search={{
          label: "Search deliveries",
          placeholder: "Bag code or flat",
          value: q,
          onChange: setQ,
        }}
        filters={[
          { key: "show", label: "Show", value: show, options: SHOW },
          {
            key: "society",
            label: "Society",
            value: society,
            options: [
              { value: "all", label: "All societies" },
              ...societies.map(([value, label]) => ({ value, label })),
            ],
          },
        ]}
        onFilterChange={(key, value) =>
          key === "show" ? setShow(value === "all" ? "all" : "open") : setSociety(value)
        }
        active={active}
        onRemove={(key) => (key === "show" ? setShow("all") : setSociety("all"))}
        onClear={() => {
          setShow("all");
          setSociety("all");
        }}
        count={rows.length}
        labels={{ results: "{n} bags" }}
      />

      <DataTable
        columns={COLUMNS}
        rows={rows}
        caption="Bags with their state and proof"
        footer={`${rows.length} of ${items.length} bags shown${sentence}.`}
        labels={{ empty: "No bags match these filters." }}
      />
    </>
  );
}
